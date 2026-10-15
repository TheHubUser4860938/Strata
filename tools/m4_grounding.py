#!/usr/bin/env python3
"""m4_grounding - does baking ABSOLUTE DATES into extracted facts make the model order events right?

The mechanism under test is the shipped dated EXTRACT_PROMPT (tools/curate_loop.py): it tells the
extractor the day the conversation happened and demands every relative date resolve into the FACT
text. `extract_prompt(t, k, dated=False)` reproduces the pre-grounding prompt byte-for-byte
(sha256 e7c8fa6b..., pinned by tools/test_curate_grounding.py), which is what makes this a
single-variable test instead of a rewrite plus a hope. The prompt mechanism itself was smoked on
10 dev-store turns (2026-10-15): dated facts come out "On 2026-10-15, ..." and undated ones don't.

What was never measured is the thing the queue is priced on: the ANSWER-level effect on the venue's
worst type. The failure anatomy says temporal-reasoning is 69 wrong of 127 with 50 of those at FULL
session coverage, and the mutation probe says the gold row present in the block is carrying the
answer 0.808 of the time - so the evidence is there and the model still fails to ORDER it, which is
the one job dates are for.

Two arms, same 127 temporal questions, same sessions, same model, same decode (temp 0.8, 256 tok,
reasoning off, memory muted - the shipped loop's own channel), two scratch stores built IN-PROCESS:

  old    facts extracted with the pre-grounding prompt (the control, byte-identical prompt)
  new    facts extracted with the shipped dated prompt

Nothing else differs: same pairing of turns, same parse regex, same per-scope fact dedupe, same
guard=False writes, same scope scheme (lme_<qid>, question-private like the venue). The curation
loop's cold-score/classify stage is deliberately NOT run: it would add two more model-dependent
divergences (probe answers, supersede targeting) between arms that are testing the prompt, not the
verdict. Facts that parse land as plain remember() writes, guard aside - the same reason
lme_harness.ingest writes with guard=False: this is evidence, and a folded row shuffles the map.

Scope of the run, measured before writing a line of code: 127 temporal questions -> 1,615 user/
assistant pairs per arm (3,228 messages; the venue verbatim ingest wrote 3,231 rows for the same
sessions). The embedder is bge-small-en-v1.5 ONNX, CPU-only, 33M params (serve/embedder.py) - a
second in-process copy costs ~150 MB, which the 2.7 GB headroom survives, and it never touches the
GPU the engine owns. No venue boot, no second server: the current :8005 answers the extraction asks
and the scoring asks, with memory muted on every one.

Score stage: per question, render the block from each scratch store (192/48k - the budget the filed
arm C run used, so the probe shape matches m4_mutation's), answer at temp 0.8 / 500 tok / fresh
conversation, judge with the official checker prompt locally (temp 0, 10 tok). Retrieval metrics
come from the provenance each fact carries {sid, pair, gold}: turn_top1/3, session_top1/3, and
row_coverage - all defined against the EXTRACTED store, so the arms are comparable to each other;
the venue's verbatim numbers (0.517 turn_top1, temporal 0.479 at full coverage) are the anchor of a
different kind and the absolute accuracies here are NOT expected to match them.

Falsifier, stated before the run: if the dated arm's accuracy delta over the undated arm is not
clear of the paired-discordant noise (and the protocol's >=3 repeats), date grounding stays a
retrieval-side result - it joins the shelf work as a ranking story - and the next venue window goes
to arm C1 instead. A delta that lands negative twice running kills the ingest path's value for
temporal outright.

    python3 tools/m4_grounding.py --stage ingest            # build both stores (resumable)
    python3 tools/m4_grounding.py --stage ingest --limit-q 2   # smoke two questions first
    python3 tools/m4_grounding.py --stage score             # answer-level A/B + retrieval
"""
from __future__ import annotations

import argparse, datetime, json, re, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import lme_harness as H                                          # noqa: E402
from curate_loop import extract_prompt, scrub, IDK               # noqa: E402  (the shipped prompt)
from gate1_driver import norm                                    # noqa: E402  (the shipped dedupe key)
from serve.memory import MemoryService                           # noqa: E402

# the shipped loop's parse (tools/curate_loop.py:502) - copied, not imported, because it lives
# inside main(); if the prompt format ever changes, test_curate_grounding.py moves and this moves
# with it or the arms parse nothing, which fails loudly at the smoke rather than silently.
LINE = re.compile(r"\s*FACT:\s*(.+?)\s*\|\|\s*PROBE:\s*(.+?)\s*\|\|\s*GOLD:\s*(.+?)\s*\|\|"
                  r"\s*SCOPE:\s*(user|project)\s*$", re.I)

ARMS = (("old", False), ("new", True))          # (arm name, dated-prompt flag) - control first


def session_epoch(date_str: str) -> float | None:
    """"2023-04-10 (Mon) 17:50" -> local epoch seconds, the form observed_on() wants. None says
    the stamp is unreadable, and then the dated arm's own rule ("write no date rather than a
    guessed one") keeps the arm honest instead of inventing a day."""
    s = (date_str or "").strip()
    for fmt in ("%Y-%m-%d (%a) %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(re.sub(r"\([A-Za-z]+\)", " ", s).strip(), fmt).timestamp()
        except ValueError:
            continue
    return None


def pairs_of(session: list[dict]) -> list[tuple[str, str, bool]]:
    """Journal shape: (user, assistant, gold) pairs. The oracle sessions alternate user/assistant,
    but the walk tolerates a stray same-role message by flushing the pending user - the extractor
    takes one pair per ask, exactly like the journal's turn rows."""
    out, pend = [], None
    for m in session:
        if m.get("role") == "user":
            if pend is not None:
                out.append((pend[0], "", pend[1]))
            pend = (m.get("content") or "", bool(m.get("has_answer")))
        else:
            u, ug = pend if pend is not None else ("", False)
            out.append((u, m.get("content") or "", ug or bool(m.get("has_answer"))))
            pend = None
    if pend is not None:
        out.append((pend[0], "", pend[1]))
    return out


def common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--data", default="/tmp/opencode/longmemeval/data/longmemeval_oracle.json")
    ap.add_argument("--records", default="spikes/lme/armc-20261015-1rep.jsonl",
                    help="the filed venue run: whose type labels select the pool")
    ap.add_argument("--types", default="temporal-reasoning")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--store-old", default="/tmp/opencode/ground-old")
    ap.add_argument("--store-new", default="/tmp/opencode/ground-new")
    ap.add_argument("--limit-q", type=int, default=0, help="first N questions only (smoke)")
    ap.add_argument("--max-facts", type=int, default=2, help="k in the extract ask (loop default)")


def questions(a) -> list[dict]:
    data = {q["question_id"]: q for q in json.load(open(a.data))}
    recs = [json.loads(l) for l in open(Path(ROOT / a.records) if not Path(a.records).is_absolute()
                                        else a.records)]
    types = set(a.types.split(",")) if a.types else set()
    seen, sel = set(), []
    for r in recs:
        qid = r["question_id"]
        if (not types or r.get("type") in types) and qid in data and qid not in seen:
            seen.add(qid)
            sel.append(data[qid])
    if a.limit_q:
        sel = sel[:a.limit_q]
    return sel


def store(a, arm: str) -> MemoryService:
    d = a.store_old if arm == "old" else a.store_new
    Path(d).mkdir(parents=True, exist_ok=True)
    return MemoryService(d, recall_limit=192, max_block_chars=48000,
                         embedder=__import__("serve.embedder", fromlist=["Embedder"]).Embedder(
                             str(ROOT / "Strata-data/embeddings/bge-small-en-v1.5"), threads=4))


def ckpt_path(a, stage: str, arm: str) -> Path:
    return Path(f"/tmp/opencode/ground-{stage}-{arm}.jsonl")


# ------------------------------------------------------------------ ingest ---------------------

def ingest(a) -> int:
    qs = questions(a)
    npairs = sum(sum(len(pairs_of(s)) for s in q["haystack_sessions"]) for q in qs)
    print(f"{len(qs)} questions, {npairs} pairs per arm, {len(ARMS)} arms = "
          f"{2 * npairs} extraction asks (temp 0.8 / 256 tok / muted) - the cost of this arm",
          flush=True)
    for arm, dated in ARMS:
        done = set()
        cp = ckpt_path(a, "ingest", arm)
        cp.parent.mkdir(parents=True, exist_ok=True)
        if cp.exists():
            for ln in open(cp):
                try:
                    r = json.loads(ln)
                    done.add((r["qid"], r["sid"], r["pair"]))
                except (json.JSONDecodeError, KeyError):
                    continue
        m = store(a, arm)
        cf = open(cp, "a")
        facts_total = fails = skipped = 0
        for qi, q in enumerate(qs, 1):
            qid, scope = q["question_id"], f"lme_{q['question_id']}"
            seen: set[str] = set()                  # the loop's per-run fact dedupe, per question:
            # production dedupes globally, but every question owns its scope here (venue shape),
            # and a global seen would starve one question of a fact another question stored first
            for si, session in enumerate(q["haystack_sessions"]):
                sid = q["haystack_session_ids"][si]
                ts = session_epoch(q["haystack_dates"][si])
                for pi, (u, asst, gold) in enumerate(pairs_of(session)):
                    if (qid, sid, pi) in done:
                        skipped += 1
                        continue
                    prompt = extract_prompt({"user": u, "assistant": asst, "created_at": ts},
                                            a.max_facts, dated=dated)
                    try:
                        r = H.post(a.url, "/v1/chat/completions",
                                   {"model": "", "messages": [{"role": "user", "content": prompt}],
                                    "max_tokens": 256, "temperature": 0.8, "reasoning_effort": "off"},
                                   headers={"X-Strata-Memory": "off"}, timeout=300.0)
                        ans = (r["choices"][0]["message"].get("content") or "").strip()
                    except Exception as e:                                        # noqa: BLE001
                        fails += 1                                                # no ckpt line: retry
                        print(f"  {arm} {qid[:14]} {sid} p{pi}: ASK FAIL {type(e).__name__}: {e}",
                              flush=True)
                        continue
                    ids, out = [], []
                    for ln in ans.splitlines():
                        mt = LINE.match(ln)
                        if not mt:
                            continue
                        fact, probe, gold_txt = (scrub(g).strip() for g in mt.groups()[:3])
                        key = norm(fact)
                        if not fact or not probe or not gold_txt or key in seen:
                            continue
                        if norm(gold_txt) in IDK:             # the shipped skip: an idk-gold is not a fact
                            continue
                        seen.add(key)
                        w = m.remember(fact, scope=scope, source=f"ground-{arm}", guard=False,
                                       provenance={"sid": sid, "pair": pi, "gold": gold})
                        if w.get("deduped") == "semantic":    # guard=False must be honored (lme_harness law)
                            raise RuntimeError(f"{qid}: guard=False folded a write into row {w['id']}")
                        ids.append(w["id"])
                        out.append(fact[:110])
                        facts_total += 1
                    cf.write(json.dumps({"qid": qid, "sid": sid, "pair": pi, "gold": gold,
                                         "ids": ids, "facts": out}, ensure_ascii=False) + "\n")
                    cf.flush()
            print(f"  {arm} {qi:>3}/{len(qs)} {qid[:14]} facts so far {facts_total}", flush=True)
        cf.close()
        st = m.stats()
        print(f"=== arm {arm}: {facts_total} facts written, {fails} ask fails, {skipped} pairs "
              f"resumed; store {st.get('memories')} memories, unembedded {st.get('unembedded')}",
              flush=True)
        m.close()
    return 0


# ------------------------------------------------------------------ score --------------------

def score(a) -> int:
    qs = questions(a)
    res: dict[str, dict] = {}
    for arm, _dated in ARMS:
        m = store(a, arm)
        cp = ckpt_path(a, "score", arm)
        cp.parent.mkdir(parents=True, exist_ok=True)
        done: dict[str, dict] = {}
        if cp.exists():
            for ln in open(cp):
                try:
                    r = json.loads(ln)
                    done[r["qid"]] = r
                except json.JSONDecodeError:
                    continue
        cf = open(cp, "a")
        for qi, q in enumerate(qs, 1):
            qid, scope = q["question_id"], f"lme_{q['question_id']}"
            if qid in done:
                res.setdefault(arm, {})[qid] = done[qid]
                continue
            m._blocks.clear()                       # one line, one fresh pin (the budget is not in the key)
            _b, shown, _qq = m.block_for([{"role": "user", "content": q["question"]}], scope,
                                         semantic=True)
            # provenance straight from the db, never from the pin-cache dicts (they come back id-only
            # on a pin hit - the trap that has produced a wrong headline before)
            ids = [h["id"] for h in shown]
            prov = {}
            if ids:
                for rid, pj in m.conn.execute(f"select id, provenance from memories where id in "
                                              f"({','.join('?' * len(ids))})", ids):
                    try:
                        prov[rid] = json.loads(pj or "{}")
                    except json.JSONDecodeError:
                        prov[rid] = {}
            gold_tot = m.conn.execute("select count(*) from memories where scope=? and invalid_at is null "
                                      "and json_extract(provenance,'$.gold')=1", (scope,)).fetchone()[0]
            asids = set(q.get("answer_session_ids") or [])
            g = [prov.get(i, {}).get("gold") for i in ids]
            s = [prov.get(i, {}).get("sid") for i in ids]
            if shown:
                blk, _ = m._render_block(shown, m.max_block_chars, scope)
                msgs = [{"role": "system", "content": blk},
                        {"role": "user", "content": f"{q['question']}\n\n{H.ANSWER_RULES['con']}"}]
            else:
                msgs = [{"role": "user", "content": f"{q['question']}\n\n{H.ANSWER_RULES['con']}"}]
            try:
                r = H.post(a.url, "/v1/chat/completions",
                           {"model": "", "messages": msgs, "max_tokens": 500, "temperature": 0.8,
                            "reasoning_effort": "off"},
                           headers={"X-Strata-Memory": "off"}, timeout=300.0)
                hyp = (r["choices"][0]["message"].get("content") or "").strip()
            except Exception:                                                       # noqa: BLE001
                hyp = ""
            rec = {"qid": qid, "arm": arm, "answer": hyp[:400], "right": H.judge(a.url, q, hyp),
                   "idk": "idk" in hyp.strip().lower().split("\n")[-1], "empty": hyp == "",
                   "notes": len(ids), "gold_rows": sum(1 for x in g if x), "gold_total": gold_tot,
                   "row_cov": (sum(1 for x in g if x) / gold_tot) if gold_tot else None,
                   "turn_top1": bool(g and g[0]), "turn_top3": any(g[:3]),
                   "sess_top1": bool(s and s[0] in asids), "sess_top3": any(x in asids for x in s[:3])}
            res.setdefault(arm, {})[qid] = rec
            cf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            cf.flush()
            cov_s = "-" if rec["row_cov"] is None else f"{rec['row_cov']:.2f}"
            print(f"  {arm} {qi:>3}/{len(qs)} {qid[:14]} {'R' if rec['right'] else 'w'}"
                  f"{' (idk)' if rec['idk'] else ''} notes {rec['notes']:>3} cov {cov_s}",
                  flush=True)
        cf.close()
        m.close()
    report(a, res)
    return 0


def report(a, res: dict) -> None:
    n = len(res.get("new", {}))
    print(f"\n  === {n} temporal questions, answer-level A/B (temp 0.8, muted, checker-judged) ===")
    for arm in ("old", "new"):
        r = list(res.get(arm, {}).values())
        if not r:
            continue
        f = lambda k: sum(1 for x in r if x[k]) / len(r)                     # noqa: E731
        cov = [x["row_cov"] for x in r if x["row_cov"] is not None]
        print(f"  {arm:<4} acc {f('right'):.3f}  idk {f('idk'):.3f}  notes {sum(x['notes'] for x in r)/len(r):6.1f}  "
              f"cov {sum(cov)/max(1, len(cov)):.3f}  turn_top1 {f('turn_top1'):.3f}  "
              f"turn_top3 {f('turn_top3'):.3f}  sess_top1 {f('sess_top1'):.3f}")
        if f("empty"):
            print(f"      EMPTY completions {sum(1 for x in r if x['empty'])}: the probe is not measuring "
                  f"what it claims")
    if len(res.get("old", {})) == len(res.get("new", {})):
        b = sum(1 for q in res["new"] if res["new"][q]["right"] and not res["old"].get(q, {}).get("right"))
        c = sum(1 for q in res["old"] if res["old"][q]["right"] and not res["new"].get(q, {}).get("right"))
        print(f"\n  paired: dated-right-only {b}  undated-right-only {c}  delta {(b - c) / max(1, n):+.3f}")
        print(f"  read as: b - c is the effect; b and c are its noise floor. One run of 127 lines cannot "
              f"clear three repeats -\n  this decides which arm the NEXT venue window is priced for, "
              f"not a release claim.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", choices=("ingest", "score"), required=True)
    common(ap)
    a = ap.parse_args()
    t0 = time.time()
    rc = ingest(a) if a.stage == "ingest" else score(a)
    print(f"elapsed {time.time() - t0:.0f}s", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
