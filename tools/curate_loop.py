#!/usr/bin/env python3
"""tools/curate_loop.py - T5-M1 spike: the curation loop over the journal backlog (plan §11).

What is missing today, measured 2026-10-11: the store journals every conversation turn but
nothing processes them - 429 of 429 journaled turns PENDING (GET /memory stats). This spike IS
the missing loop, end to end:

  drain turns_pending (POST /memory {"op":"turns"})
  -> EXTRACT question-shaped candidates (one muted generation per turn: FACT || PROBE || GOLD
     || SCOPE lines; the self-probe step the scorer forces - a free-text fact is only
     scoreable once the model can state a question only that fact answers)
  -> COLD-SCORE every candidate: 3 passes, probe asked muted on the same channel discipline
     the C-series banked (reasoning off, temperature 0.8, X-Strata-Memory: off, geometry
     usage.prompt_tokens == local render else that ask is VOID for that candidate)
  -> FOUR-WAY novelty sort (plan §11):
       FILL        0/3 gold, answers idk           -> weights cannot reach it: STORE
       STABILIZE   1-2/3 gold                      -> weak/unstable prior: STORE
       CORRECT     0/3 gold, all 3 CONFABULATE     -> memory must OVERRIDE a wrong prior:
                                                     STORE flagged (priority injection later)
       DONT-STORE  3/3 gold                        -> weights already own it: log only
     (0/3 with a MIX of idk and confabulation stays FILL with a mixed flag - the confab-vs-idk
     line is the claim, a half-shimmer answer does not support it)
  -> remember(source="curation-<date>", provenance={turn_id, probe, gold, cold answers, cls})
     via POST /memory - the review list is then just "list by source tag"
  -> mark turns processed (direct SQLite: WAL + busy_timeout 5000 per serve/memory.py:114-118,
     cross-process writes are safe; the product form moves this to a server-side job with a
     "mark" op)
  -> print the review list grouped by class.

Modes: default = one-shot drain of everything pending (--limit caps turns this run).
--sweep = the hot-loop shape: batches gated on engine idle (GET /v1/status not busy) so the
loop never interrupts work; --interval seconds between checks. --extract-only stops after
extraction (smoke the extractor before burning cold-score asks). --dry-run renders and counts
asks, touches no engine.

Honest-cost note: the backlog drain is ~1 extraction ask per turn + 3 cold asks per candidate;
429 turns at ~2 candidates/turn is on the order of 3,000 asks ~ 45-70 min of idle engine time,
run once. The daily steady state is ~30-50 turns ~ 2-4 min spread over idle gaps.

State: tools/state/curate_<tag>.jsonl + review_<tag>.txt carry USER-DERIVED TEXT (facts, probes,
answers) - deliberately UNTRACKED by git (personal-hygiene policy); the evidence that lands in
git is the aggregate counts + the verdict block, never raw personal text.
"""
from __future__ import annotations
import argparse, datetime, json, re, sqlite3, sys, time
import urllib.error, urllib.request as U
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "spikes" / "window"))
sys.path.insert(0, str(ROOT / "spikes" / "d2_dissect"))
from venue_render import load_tokenizer, load_template, live_render   # server-exact render
from gate1_driver import hit, norm                                    # the strict scorer

COLD_PREAMBLE = ("Answer the question with the shortest exact answer - a name, number, date, "
                 "or short phrase. If you do not know, answer exactly: idk. No guessing, no "
                 "sentences, no explanation.\n\nQuestion:")
EXTRACT_PROMPT = (
    "You are scanning one work conversation for durable facts worth remembering. List up to "
    "{k} facts that a fresh assistant session could NOT already know: personal details about "
    "the user, or project-specific findings and decisions. Skip generic chatter, small talk, "
    "tool noise, and anything answerable from general knowledge. For each fact output ONE "
    "line exactly in this format:\n"
    "FACT: <one sentence> || PROBE: <a question only this fact answers> || GOLD: <the "
    "shortest exact answer to PROBE> || SCOPE: user|project\n"
    "No other lines, no numbering, no commentary.\n\nCONVERSATION:\nuser: {user}\n"
    "assistant: {assistant}")
# the idk set mirrors gate1_driver.hit()'s idk guard (spikes/window/gate1_driver.py) - the
# SAME predicate that zeroes a cold answer must decide confab-vs-idk, or the two disagree.
IDK = ("idk", "i don't know", "i do not know", "unknown", "don't know", "no answer", "")


def http(url, payload=None, headers=None, timeout=960):
    data = json.dumps(payload).encode() if payload is not None else None
    req = U.Request(url, data=data, method="POST" if data else "GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with U.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {"raw": "<unreadable error body>"}
    except Exception as e:                                  # transport: recorded, not crashed
        return 0, {"error": {"message": f"transport: {type(e).__name__}: {e}"[:160]}}


def idk_p(answer: str) -> bool:
    return norm(answer) in IDK


def classify(cold: list[dict]) -> tuple[str, bool]:
    """cold = surviving (geometry-OK) scored passes [{"gold_hit":b,"idk":b},...].
    Returns (class, mixed_flag). CORRECT requires ALL passes confidently wrong - the claim is
    'the model asserts a wrong answer', a half-shimmer run does not support it (plan §11)."""
    hits = sum(1 for c in cold if c["gold_hit"])
    if hits == len(cold):
        return "DONT-STORE", False
    if hits >= 1:
        return "STABILIZE", False
    confab = [c for c in cold if not c["idk"]]
    if len(confab) == len(cold):
        return "CORRECT", False
    return "FILL", bool(confab)                             # 0/3 with any confab = mixed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--limit", type=int, default=0, help="max turns this run (0 = all pending)")
    ap.add_argument("--passes", type=int, default=3, help="cold passes per candidate (>= 3 protocol)")
    ap.add_argument("--max-facts", type=int, default=2, help="candidate facts extracted per turn")
    ap.add_argument("--sweep", action="store_true", help="idle-gated batches (hot-loop shape)")
    ap.add_argument("--interval", type=int, default=60, help="--sweep: seconds between idle checks")
    ap.add_argument("--batches", type=int, default=0, help="--sweep: stop after N batches (0 = forever)")
    ap.add_argument("--extract-only", action="store_true", help="smoke the extractor, skip cold-score")
    ap.add_argument("--no-store", action="store_true", help="score but do not remember/mark")
    ap.add_argument("--dry-run", action="store_true", help="render + count, no engine asks")
    ap.add_argument("--tag", default=datetime.date.today().isoformat())
    a = ap.parse_args()
    a.tag = re.sub(r"[^A-Za-z0-9_-]", "_", a.tag)           # tag lands in filenames (c2 rule)
    if a.passes < 3:
        raise SystemExit("--passes >= 3 is mandatory protocol (V3/C-series: >= 3 passes)")
    url = a.url.rstrip("/")
    state = HERE / "state"
    state.mkdir(exist_ok=True)
    js = (state / f"curate_{a.tag}.jsonl").open("a")

    def rec(obj):
        obj = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "tag": a.tag, **obj}
        js.write(json.dumps(obj, ensure_ascii=False) + "\n")
        js.flush()
        print(json.dumps({k: v for k, v in obj.items() if k not in ("ts", "tag")},
                         ensure_ascii=False)[:220], flush=True)

    st, health = http(url + "/health", timeout=15)
    if st != 200:
        raise SystemExit(f"/health -> {st}: no run")
    model = health.get("model") or "strata"
    st, resp = http(url + "/memory")
    if st != 200:
        raise SystemExit("GET /memory failed - is memory on?")
    stats = resp.get("stats") or {}                        # GET /memory = {"stats": {...}, "memories": [...]}
    db_path = stats.get("db")
    if not db_path:
        raise SystemExit("stats.db missing - cannot mark turns processed")
    print(f"health ok model={model} tag={a.tag} turns_pending={stats.get('turns_pending')} "
          f"memories={stats.get('memories')} channel=off/temp0.8 muted passes={a.passes}")

    tok, tpl = load_tokenizer(), load_template()

    def ask(text, maxtok, want_geom=None, kind=""):
        """one muted generation; returns (answer, ptok, geom_ok) - transport failure -> (None,..)."""
        body = {"model": model, "messages": [{"role": "user", "content": text}],
                "temperature": 0.8, "reasoning_effort": "off", "max_tokens": maxtok,
                "stream": False}
        st, r = http(url + "/v1/chat/completions", body, headers={"X-Strata-Memory": "off"})
        if st != 200:
            return None, None, False
        ans = (((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        ptok = (r.get("usage") or {}).get("prompt_tokens")
        geom = None if want_geom is None else (ptok == want_geom)
        return ans, ptok, geom

    # ---- extraction ----------------------------------------------------------------------
    processed_ids: list[int] = []
    candidates: list[dict] = []
    seen: set[str] = set()
    drained = fails = 0
    batch = 0
    while True:
        # idle gate (hot-loop shape): never extract into the middle of someone's request
        if a.sweep and batch:
            time.sleep(a.interval)
        if a.sweep:
            st, svc = http(url + "/v1/status", timeout=15)
            if svc.get("busy") or (svc.get("queued") or 0) > 0:
                print("engine busy - sweep waits", flush=True)
                continue
        st, r = http(url + "/memory", {"op": "turns", "limit": 50}, timeout=60)
        turns = (r.get("turns") or []) if st == 200 else []
        if a.limit:
            turns = turns[:max(0, a.limit - drained)]
        if not turns:
            break
        for t in turns:
            prompt = EXTRACT_PROMPT.format(k=a.max_facts, user=(t["user"] or "")[:1200],
                                           assistant=(t["assistant"] or "")[:1600])
            ids, _ = live_render(tok, tpl, [{"role": "user", "content": prompt}], "off")
            if a.dry_run:
                print(f"turn {t['id']}: extract ask renders to {len(ids)} tok")
                drained += 1
                continue
            ans, ptok, _ = ask(prompt, 256, kind="extract")
            if ans is None:
                fails += 1
                print(f"turn {t['id']}: extract TRANSPORT FAIL (left pending)", flush=True)
                continue
            n = 0
            for ln in ans.splitlines():
                m = re.match(r"\s*FACT:\s*(.+?)\s*\|\|\s*PROBE:\s*(.+?)\s*\|\|\s*GOLD:\s*"
                             r"(.+?)\s*\|\|\s*SCOPE:\s*(user|project)\s*$", ln, re.I)
                if not m:
                    continue
                fact, probe, gold, scope = (g.strip() for g in m.groups())
                key = norm(fact)
                if not fact or not probe or not gold or key in seen:
                    continue
                if norm(gold) in IDK:
                    # a candidate whose GOLD is "idk" is not a fact: the extractor proposed a
                    # question its own answer admits it cannot answer. And gate1.hit()'s idk
                    # guard (spikes/window/gate1_driver.py) returns False BEFORE gold matching,
                    # so an idk-gold could never score a hit - it would silently misclassify.
                    continue
                seen.add(key)
                n += 1
                candidates.append({"turn_id": t["id"], "turn_scope": t["scope"], "fact": fact,
                                   "probe": probe, "gold": gold,
                                   "scope": "user" if scope.lower() == "user" else t["scope"],
                                   "extract_ptok": ptok})
            processed_ids.append(t["id"])
            drained += 1
            rec({"kind": "extract", "turn_id": t["id"], "cands": n, "ptok": ptok,
                 "facts": [c["fact"][:90] for c in candidates[-n:]]})
        batch += 1
        if a.dry_run or (a.batches and batch >= a.batches) or (a.limit and drained >= a.limit):
            break
    if a.dry_run:
        print(f"dry run: {drained} extract asks queued ({len(candidates)} candidates shown "
              f"are placeholders - no engine touched)")
        return 0
    print(f"extracted: {drained} turns drained, {len(candidates)} unique candidates, "
          f"{fails} transport fails", flush=True)
    if a.extract_only:
        for c in candidates:
            print(f"  [{c['turn_id']}] {c['fact'][:100]}")
        return 0

    # ---- cold score ----------------------------------------------------------------------
    stored = {"FILL": 0, "STABILIZE": 0, "CORRECT": 0, "DONT-STORE": 0}
    unscored = 0
    review: list[str] = []
    for c in candidates:
        text = f"{COLD_PREAMBLE}\n{c['probe']}"
        ids, _ = live_render(tok, tpl, [{"role": "user", "content": text}], "off")
        want = len(ids)
        cold = []
        for p in range(a.passes):
            ans, ptok, geom = ask(text, 24, want_geom=want, kind="cold")
            if ans is None:
                rec({"kind": "cold", "turn_id": c["turn_id"], "pass": p + 1, "transport": "fail"})
                continue
            if not geom:                                   # per-ask VOID (V3 precedent): the
                rec({"kind": "cold", "turn_id": c["turn_id"], "pass": p + 1, "void": True,
                     "ptok": ptok, "expect": want})        # candidate's band needs >= 3 alive
                continue
            g = bool(hit(ans, c["gold"]))
            cold.append({"gold_hit": g, "idk": idk_p(ans), "answer": ans[:90], "ptok": ptok})
        if len(cold) < a.passes:
            unscored += 1
            cls, mixed, full = "UNSCORED", False, None
        else:
            cls, mixed = classify(cold)
            full = cold
        c["cls"], c["mixed"], c["cold"] = cls, mixed, full
        if cls != "UNSCORED" and not a.no_store:
            st, r = http(url + "/memory", {
                "op": "remember", "text": c["fact"], "scope": c["scope"],
                "source": f"curation-{datetime.date.today().isoformat()}",
                "provenance": {"turn_id": c["turn_id"], "probe": c["probe"], "gold": c["gold"],
                               "cls": cls, "mixed": mixed, "passes": cold, "model": model}},
                timeout=60)
            c["mem_id"] = (r or {}).get("id")
            if st != 200:
                rec({"kind": "remember", "turn_id": c["turn_id"], "http": st,
                     "err": str(r)[:120]})
        stored[cls] = stored.get(cls, 0) + 1
        rec({"kind": "score", "turn_id": c["turn_id"], "cls": cls, "mixed": mixed,
             "fact": c["fact"][:90], "gold": c["gold"][:40],
             "hits": [x["gold_hit"] for x in cold]})
        review.append(f"[{cls}{'/mixed' if mixed else ''}] (turn {c['turn_id']}) {c['fact']}\n"
                      f"    probe: {c['probe']}  gold: {c['gold']}  "
                      f"cold: {' | '.join(x['answer'][:40] for x in cold)}")
    if processed_ids and not a.no_store:                   # the store's own hygiene flag
        con = sqlite3.connect(db_path, timeout=10)
        con.executemany("update turns set processed=1 where id=?", [(i,) for i in processed_ids])
        con.commit()
        con.close()

    # ---- verdict block (the part that is safe to commit: counts, no personal text) --------
    pend_after = (http(url + "/memory", timeout=15)[1].get("stats") or {}).get("turns_pending")
    lines = [f"curate verdict tag={a.tag} channel=off/temp0.8 muted passes={a.passes} "
             f"stored={'off' if a.no_store else 'on'}",
             f"  drained={drained} extract_fails={fails} candidates={len(candidates)} "
             + " ".join(f"{k}={v}" for k, v in stored.items()) + f" UNSCORED={unscored}",
             f"  marked_processed={len(processed_ids) if not a.no_store else 0} "
             f"db_pending_after={pend_after}"]
    txt = "\n".join(lines)
    print("\n" + txt)
    (state / f"curate_{a.tag}.review.txt").write_text("\n".join(review) + "\n\n" + txt + "\n",
                                                      encoding="utf-8")
    js.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
