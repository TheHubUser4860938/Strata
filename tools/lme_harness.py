#!/usr/bin/env python3
"""LongMemEval-V1 venue harness for Strata's built-in memory (beta item F, 2026-10-13).

The claim F must measure before beta says "yes": the server's own recall answers real
long-term-memory questions from a history it ingested. The benchmark (LongMemEval, ICLR
2025, MIT; data at huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) fits the
machinery one-to-one: 500 questions, each with a timestamped chat history, a gold answer,
turn-level `has_answer` gold labels, and 30 abstention ids ending in `_abs`.

Design decisions, recorded where they bite:

- PER-QUESTION SCOPE (`lme_<question_id>`). Personas differ per question and the USR lane
  is global; pinning each question's facts to its own project scope is the contamination
  guard the plan pre-registered. The server recalls with the same header any client uses
  (X-Strata-Memory-Scope), so this measures the product path, not a harness-side RAG.
- INGEST = `op remember` per turn, date-prefixed (temporal reasoning needs dates and the
  store's created_at would say "today"). Both roles: `single-session-assistant` questions
  hide their evidence in assistant turns. Pure SQL - the engine never runs for ingest,
  and the returned row id maps every recall hit back to its turn and session, which is
  how top-1/top-3 (the Primnox metric) is scored against `has_answer` and
  `answer_session_ids`.
- ANSWERS ride the live engine: fresh conversation per ask, temp 0.8 (never 0), >=3
  repeats per question with a majority verdict, probe max_tokens 500. Muted vs unmuted is
  the A/B: --muted-control runs the same question with memory off for the cold baseline.
- JUDGE: the official script wants gpt-4o; this box has no key, so the judge is LOCAL -
  the same model, memory MUTED, asked yes/no against the gold - and the metrics file says
  so out loud (disclosure is part of the claim).
- Scrub at the choke: every string written out passes the pipe-splitter (the serving
  template's delimiter must never re-enter any file a later prompt reads back).

Venue-day protocol: dedicated store (memory.dir to a venue dir, max_memories 0 - the
oracle set alone is ~6k rows), nothing else on the port, pilot --limit 5 first, then the
full run backgrounded (resumable: completed questions are skipped from --out).

Usage:
  python3 tools/lme_harness.py --data longmemeval_oracle.json --out lme_oracle.jsonl \
      [--url http://127.0.0.1:8005] [--limit N] [--repeats 3] [--recall-k 10] \
      [--types single-session-user,...] [--skip-ingest] [--no-judge] [--muted-control]
"""
from __future__ import annotations

import argparse, json, re, sys, time, urllib.error, urllib.parse, urllib.request as U

DATE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})")
ANSWER_RULES = {
    "direct": ("Answer in a few words from the memory you carry. If it does not hold what "
               "is needed, reply exactly: idk"),
    # The benchmark authors' own recommendation ("con": extract useful information first, then
    # reason over it) - the oracle pilot (2026-013-13) showed the failure it targets: top-1
    # recall was 3/3 while one answer still picked the wrong fact out of the block.
    "con": ("First quote the memory lines that bear on the question, then give the final answer "
            "in a few words on its own last line. If the memory does not hold what is needed, "
            "reply exactly idk on the last line."),
}


def scrub(s: str) -> str:
    """The standing rule for anything that leaves into a file a prompt may read back: the
    chat template's delimiter can never travel intact through venue output."""
    return (s or "").replace("<|", "< |").replace("|>", "| >")


def post(url: str, path: str, body: dict, headers: dict | None = None, timeout: float = 240.0):
    req = U.Request(url + path, data=json.dumps(body).encode(), method="POST",
                    headers={"Content-Type": "application/json", **(headers or {})})
    with U.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get(url: str, path: str, params: dict, headers: dict | None = None, timeout: float = 60.0):
    q = urllib.parse.urlencode(params)
    req = U.Request(f"{url}{path}?{q}", headers=headers or {})
    with U.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def day_of(date_str: str) -> str:
    m = DATE_RE.match(date_str or "")
    return m.group(1) if m else ""


def ingest(url: str, q: dict, scope: str) -> dict[int, tuple[bool, str]]:
    """Remember every turn of every haystack session, date-prefixed, into the question's own
    scope. Returns row-id -> (has_answer, session_id) so recall hits score turn- AND
    session-level."""
    where: dict[int, tuple[bool, str]] = {}
    for si, session in enumerate(q["haystack_sessions"]):
        sid = q["haystack_session_ids"][si]
        day = day_of(q["haystack_dates"][si])
        for turn in session:
            text = turn.get("content") or ""
            if not text.strip():
                continue
            lead = f"[{day}] " if day else ""
            if turn.get("role") != "user":
                lead += "The assistant told the user: "
            r = post(url, "/memory", {"op": "remember", "text": lead + text, "scope": scope})
            rid = r.get("id")
            if rid is not None:
                where[rid] = (bool(turn.get("has_answer")), sid)
    return where


def recall(url: str, q: dict, scope: str, where: dict, k: int) -> dict:
    """The Primnox metric on the server's own search: top-1/top-3 at turn level (the first
    / first-three hits carrying a has_answer turn) and at session level (hits from an
    answer_session). Abstention questions have no gold location and report null, as the
    official retrieval eval skips them."""
    # GET /memory?q= answers under "hits" (the list endpoint uses "memories") - reading the
    # wrong key would score a quiet zero instead of an honest miss.
    hits = get(url, "/memory", {"q": q["question"], "scope": scope, "limit": k},
               headers={"Accept": "application/json"}).get("hits", [])
    ids = [h["id"] for h in hits]
    abstain = q["question_id"].endswith("_abs")
    gold_sids = set(q.get("answer_session_ids") or [])

    def flag(fn):
        if abstain or not ids:
            return None
        vals = [where.get(i, (False, "")) for i in ids[:k]]
        return fn(vals)

    return {"ids": ids,
            "turn_top1": flag(lambda v: v and v[0][0]),
            "turn_top3": flag(lambda v: any(x[0] for x in v[:3])),
            "session_top1": flag(lambda v: v and v[0][1] in gold_sids),
            "session_top3": flag(lambda v: any(x[1] in gold_sids for x in v[:3]))}


def ask(url: str, q: dict, scope: str, muted: bool, style: str = "direct") -> str:
    """One fresh conversation, one answer. The server injects from the question's own scope
    (that IS the product path); a muted control request carries the header that turns
    memory off, and the cold baseline gets no scope at all."""
    hdrs = ({"X-Strata-Memory": "off"} if muted
            else {"X-Strata-Memory-Scope": scope})
    body = {"model": "", "messages": [{"role": "user",
                                       "content": f"{q['question']}\n\n{ANSWER_RULES[style]}"}],
            "max_tokens": 500, "temperature": 0.8, "reasoning_effort": "off"}
    r = post(url, "/v1/chat/completions", body, headers=hdrs)
    return (r["choices"][0]["message"].get("content") or "").strip()


def judge(url: str, q: dict, hypothesis: str) -> bool:
    """Local self-judge, disclosed as such: the same model with memory MUTED (the judge must
    see only the three texts) decides semantic correctness. Temp 0.8 per the standing
    discipline; a hangup or junk counts as incorrect, never as a free pass."""
    prompt = (f"Question: {q['question']}\nGold answer: {q['answer']}\n"
              f"Model answer: {hypothesis or '(empty)'}\n"
              "Treat the model answer's LAST line as its answer; earlier lines may be quoted "
              "evidence. Is that answer semantically correct for the gold? Reply exactly yes or no.")
    try:
        r = post(url, "/v1/chat/completions",
                 {"model": "", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 500, "temperature": 0.8, "reasoning_effort": "off"},
                 headers={"X-Strata-Memory": "off"})
        out = (r["choices"][0]["message"].get("content") or "").strip().lower()
    except Exception:                                       # noqa: BLE001 - a failed judge says no
        return False
    return out.startswith("yes")


def load_map(map_path: str) -> dict[str, dict[int, tuple[bool, str]]]:
    """Question -> row-id -> (has_answer, session_id), persisted at ingest so a resumed run
    never re-ingests and --skip-ingest never scores against a guessed map (the store does
    not carry the benchmark's gold labels; this sidecar does)."""
    maps: dict[str, dict[int, tuple[bool, str]]] = {}
    try:
        for line in open(map_path):
            o = json.loads(line)
            maps[o["question_id"]] = {int(kk): (v[0], v[1]) for kk, v in o["where"].items()}
    except FileNotFoundError:
        pass
    return maps


def run(url: str, data: list[dict], out_path: str, repeats: int, k: int, types: set,
        skip_ingest: bool, use_judge: bool, muted_control: bool, limit: int,
        style: str = "direct", map_path: str | None = None, log=print) -> dict:
    done, map_path = {}, map_path or out_path + ".map"
    try:
        for line in open(out_path):
            o = json.loads(line)
            done.setdefault(o["question_id"], set()).add(o["repeat"])
    except FileNotFoundError:
        pass
    maps = load_map(map_path)
    stats, started = {"questions": 0, "lines": 0}, time.time()
    with open(out_path, "a") as out, open(map_path, "a") as mapf:
        for q in data:
            if limit and stats["questions"] >= limit:
                break
            qid, qtype = q["question_id"], q["question_type"]
            if types and qtype not in types:
                continue
            if qid.endswith("_abs"):
                qtype = "abstention"
            scope = f"lme_{qid}"
            if len(done.get(qid, ())) >= repeats:
                continue
            if qid in maps:
                where = maps[qid]
            elif skip_ingest:
                raise SystemExit(f"{qid}: no gold map for {qid} - --skip-ingest needs a map "
                                 f"from an earlier run ({map_path})")
            else:
                where = ingest(url, q, scope)
                maps[qid] = where
                mapf.write(json.dumps({"question_id": qid,
                                       "where": {str(i): list(v) for i, v in where.items()}}) + "\n")
                mapf.flush()
                log(f"{qid} ({qtype}): ingested {len(where)} turns")
            rec = recall(url, q, scope, where, k)
            correct_votes = []
            for rep in range(repeats):
                if rep in done.get(qid, ()):
                    continue
                hyp = scrub(ask(url, q, scope, muted=False, style=style))
                cold = scrub(ask(url, q, scope, muted=True)) if muted_control else None
                ok = judge(url, q, hyp) if use_judge else None
                if ok is not None:
                    correct_votes.append(ok)
                obj = {"question_id": qid, "type": qtype, "repeat": rep,
                       "hypothesis": hyp, "gold": scrub(q["answer"]), "correct": ok,
                       "cold_hypothesis": cold, "judge": None if ok is None else
                       "local self-judge (same model, memory muted, temp 0.8)",
                       **{kk: rec[kk] for kk in ("turn_top1", "turn_top3", "session_top1",
                                                 "session_top3")}, "recall_ids": rec["ids"]}
                out.write(json.dumps(obj, ensure_ascii=False) + "\n")
                out.flush()
                stats["lines"] += 1
            stats["questions"] += 1
            majority = (sum(correct_votes) * 2 > len(correct_votes)) if len(correct_votes) >= 3 else None
            log(f"{qid} ({qtype}): top1={rec['turn_top1']} top3={rec['turn_top3']} "
                f"{'majority=' + str(majority) if majority is not None else ''}")
    print(f"[lme] {stats['questions']} questions, {stats['lines']} lines, "
          f"{time.time() - started:.0f} s; metrics: {metrics(out_path)}")
    return {**stats, "metrics": metrics(out_path)}


def metrics(out_path: str) -> dict:
    """Aggregate what the run wrote: recall top-1/top-3 (turn and session) and judge accuracy,
    overall and per type. Abstention questions keep recall nulls (skipped in the means, as the
    official retrieval eval does); the judge line says the judge was local."""
    rows = [json.loads(l) for l in open(out_path)]
    out = {"lines": len(rows), "judge": rows[0]["judge"] or "none (--no-judge)"}

    def agg(sub):
        r = {}
        for key in ("turn_top1", "turn_top3", "session_top1", "session_top3"):
            vals = [x[key] for x in sub if x[key] is not None]
            r[key] = round(sum(vals) / len(vals), 3) if vals else None
        cor = [x["correct"] for x in sub if x["correct"] is not None]
        r["accuracy"] = round(sum(cor) / len(cor), 3) if cor else None
        # con-style hypotheses can MENTION idk while answering; only the final line is the answer
        def is_idk(x):
            return "idk" in (x["hypothesis"] or "").strip().lower().split("\n")[-1]
        r["idk_rate"] = round(sum(1 for x in sub if is_idk(x)) / len(sub), 3) if sub else None
        return r

    out["overall"] = agg(rows)
    types = sorted({r["type"] for r in rows})
    out["by_type"] = {t: agg([r for r in rows if r["type"] == t]) for t in types}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LongMemEval-V1 venue harness for Strata memory")
    ap.add_argument("--data", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--out", default="lme_hypotheses.jsonl")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--recall-k", type=int, default=10)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--types", default="", help="comma-separated question_type filter")
    ap.add_argument("--skip-ingest", action="store_true", help="rows already in the store")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--muted-control", action="store_true", help="also ask each question cold")
    ap.add_argument("--style", choices=("direct", "con"), default="direct",
                    help="answer instruction: direct, or the authors' recommended extract-then-answer")
    ap.add_argument("--map", dest="map_path", default=None,
                    help="gold-map sidecar (default: OUT.map) - point at an earlier run's map to --skip-ingest")
    a = ap.parse_args(argv)
    data = json.load(open(a.data))
    st = get(a.url, "/memory", {"limit": 1}).get("stats", {})
    cap = st.get("max_memories") or 0
    if cap:
        print(f"[lme] store cap {cap} (rows {st.get('memories')}): a full run wants max_memories 0 "
              "on a dedicated venue store - the oracle set alone is ~6k rows", file=sys.stderr)
    got = run(a.url, data, a.out, a.repeats, a.recall_k, {t.strip() for t in a.types.split(",")
            if t.strip()}, a.skip_ingest, not a.no_judge, a.muted_control, a.limit, a.style, a.map_path)
    with open(a.out + ".metrics.json", "w") as f:                  # the venue record
        json.dump({"data": a.data, "url": a.url, "repeats": a.repeats, "recall_k": a.recall_k,
                   "muted_control": a.muted_control, "style": a.style, **got}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
