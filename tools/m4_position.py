#!/usr/bin/env python3
"""m4_position - is the injected memory block a LIST the model walks, or a BAG it samples from?

Why this is the next measurement: the venue record says retrieval is no longer the binding constraint.
Of 346 wrong answer lines in arm C, 244 had EVERY gold session already present in the block (reasoning
failures) and only 102 were missing one (retrieval failures). Meanwhile the reranker moved retrieval
rank-1 by 0.139 on knowledge-update questions and moved accuracy by +0.005, which was read as "at this
block size the answer does not ride on rank order". That reading was inferred, not measured, and the
speaker-demotion work was parked on it. If position does not change the answer, then every ordering or
re-ranking feature is chasing a mechanism that does not exist in the model, and the remaining budget
belongs to presentation and the reasoning path instead. If it does change the answer, ordering pays and
we know exactly how much.

Design: within-subject, order-only. For one question, the SAME set of rows goes into the block every
time - the question's own gold rows plus as many of its own non-gold rows as fit the shipped 24,000-char
budget - and only the gold rows' POSITION changes: front, middle, tail. Rows are rebuilt from the
benchmark file with the harness's own ingest law (date prefix, assistant prefix, 3,900-char word-boundary
chunking), the block is rendered by `MemoryService._render_block` itself (not a copy of its format, so
the bytes cannot drift from what ships), and the answer is scored by the benchmark's own checker run
locally. Style is pinned to `con`, the style every filed venue number was measured with, so the three
arms sit on the same footing as the 0.761 record.

Memory is muted on every request (X-Strata-Memory: off) and the block rides in the message we send, so
the store the server has open is irrelevant to the answer - and `usage.prompt_tokens` must come back
IDENTICAL across the three arms for a question, because only the order changed. That check is the run's
own guard: a differing count means the arms were not showing the same bytes.

Offline row construction, live answers: needs the engine, ~30 min for 60 questions x 3 arms x 3 repeats.
Timings are NOT valid here (this probe shares the port with the assistant's own inference); correctness
is, because the judge and the answer path are the same process either way.
"""
from __future__ import annotations

import argparse, collections, json, random, shutil, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from strata_control.datafolder import data_dir                    # noqa: E402  the one data folder (2026-10-23)
sys.path.insert(0, str(ROOT))
from serve.memory import MemoryService, scope_name                 # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
from lme_harness import ANSWER_RULES, chunk_turn, day_of, judge, post   # noqa: E402

BUDGET = 24000            # the shipped char cap (dev 96/24k; the block is char-bound at venue rows)
ARMS = ("front", "middle", "tail")


def rows_for(q: dict) -> tuple[list, list]:
    """This question's rows exactly as the venue store holds them: every turn of every haystack session,
    date-prefixed, assistant turns attributed, split by the harness's own chunker. Returns (gold, other)
    - the gold flag is the turn's, carried by every chunk of it, which is what makes the map honest."""
    gold, other = [], []
    for si, session in enumerate(q["haystack_sessions"]):
        day = day_of(q["haystack_dates"][si])
        for turn in session:
            text = turn.get("content") or ""
            if not text.strip():
                continue
            lead = f"[{day}] " if day else ""
            if turn.get("role") != "user":
                lead += "The assistant told the user: "
            row = {"scope": scope_name("lme_" + q["question_id"]), "text": ""}
            for piece in chunk_turn(lead + text):
                r = dict(row, text=piece)
                (gold if turn.get("has_answer") else other).append(r)
    return gold, other


def order_for(gold, other, arm, fit):
    """The candidate list whose RENDERED block puts the gold rows at the chosen position and still shows
    exactly `fit` notes. Tail means last SHOWN, not last candidate - a gold row trimmed out of the block
    measures retrieval, not position, and that distinction is the whole point of the arm."""
    rng = random.Random(7)                       # one fixed distractor order for all three arms
    dsel = rng.sample(other, max(0, fit - len(gold))) if other else []
    if not gold:
        return dsel
    if arm == "front":
        return list(gold) + dsel
    if arm == "tail":
        return dsel + list(gold)
    mid = max(0, (fit - len(gold)) // 2)
    return dsel[:mid] + list(gold) + dsel[mid:]


def ask(url, q, block, style):
    """One fresh conversation, memory muted, the block supplied by us. Returns (answer, prompt_tokens)."""
    r = post(url, "/v1/chat/completions",
             {"model": "", "messages": [{"role": "system", "content": block},
                                        {"role": "user",
                                         "content": f"{q['question']}\n\n{ANSWER_RULES[style]}"}],
              "max_tokens": 500, "temperature": 0.8, "reasoning_effort": "off"},
             headers={"X-Strata-Memory": "off"})
    return ((r["choices"][0]["message"].get("content") or "").strip(),
            (r.get("usage") or {}).get("prompt_tokens"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default="/tmp/opencode/longmemeval/data/longmemeval_oracle.json")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--out", default="/tmp/opencode/position-probe.jsonl")
    ap.add_argument("--questions", type=int, default=60)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--style", default="con", choices=("direct", "con"),
                    help="pinned to con: every filed venue number, 0.761 included, was measured with it")
    ap.add_argument("--types", default="", help="optional question_type filter")
    a = ap.parse_args()

    data = json.load(open(a.data))
    for q in data:
        q["answer"] = str(q["answer"])
    want_types = {t.strip() for t in a.types.split(",") if t.strip()}

    # the block's own header carries "shown X of N active" and that N must be a real store number, so
    # the renderer is run against a COPY of the dev store (read-only use: it only counts rows)
    tmp = Path("/tmp/opencode/pos-render-store")
    if tmp.exists():
        shutil.rmtree(tmp)
    src = data_dir(ROOT) / "memory-eval"
    import _storeclone
    _storeclone.clone_store(src, tmp)     # R5b: honest dump/restore; the old db+wal+shm loop
    renderer = MemoryService(str(tmp), recall_limit=96, max_block_chars=BUDGET, embedder=None)

    # stratified sample: round-robin over question types so the answer is not one type's story, and
    # abstention questions are out by construction (they have no gold row to place anywhere)
    by_type = collections.OrderedDict()
    for q in sorted(data, key=lambda x: x["question_id"]):
        if q["question_id"].endswith("_abs") or not any(
                t.get("has_answer") for s in q["haystack_sessions"] for t in s):
            continue
        if want_types and q["question_type"] not in want_types:
            continue
        by_type.setdefault(q["question_type"], []).append(q)
    picked, i = [], 0
    while len(picked) < a.questions and any(by_type.values()):
        for t in list(by_type):
            if by_type[t]:
                picked.append(by_type[t].pop(0))
                if len(picked) == a.questions:
                    break

    print(f"{len(picked)} questions over {len({q['question_type'] for q in picked})} types, "
          f"style={a.style}, budget={BUDGET} chars, repeats={a.repeats}", flush=True)
    out = open(a.out, "w")
    score = {arm: [0, 0] for arm in ARMS}
    byt = {arm: collections.defaultdict(lambda: [0, 0]) for arm in ARMS}
    tok_mismatch = fits = 0
    t0 = time.time()
    for n, q in enumerate(picked, 1):
        gold, other = rows_for(q)
        if not other:
            continue
        # how many notes actually fit: render the FULL candidate list once and count what came back
        _, shown = renderer._render_block(gold + other, BUDGET, gold[0]["scope"])
        fit = len(shown)
        fits += fit
        toks = {}
        for rep in range(a.repeats):
            for arm in ARMS:
                block, shown2 = renderer._render_block(order_for(gold, other, arm, fit),
                                                       BUDGET, gold[0]["scope"] if gold else "lme")
                hyp, ptok = ask(a.url, q, block, a.style)
                ok = judge(a.url, q, hyp)
                toks.setdefault(arm, ptok)
                score[arm][0] += 1 if ok else 0
                score[arm][1] += 1
                b = byt[arm][q["question_type"]]
                b[0] += 1 if ok else 0
                b[1] += 1
                out.write(json.dumps({"question_id": q["question_id"], "type": q["question_type"],
                                      "repeat": rep, "arm": arm, "fit": len(shown2), "ok": ok,
                                      "prompt_tokens": ptok, "gold_rows": len(gold),
                                      "answer": q["answer"], "hypothesis": hyp}) + "\n")
        if len(set(v for v in toks.values() if v)) > 1:
            tok_mismatch += 1
        out.flush()
        el = time.time() - t0
        print(f"  [{n:>3}/{len(picked)}] {q['question_id']:<22} fit={fit:>3} "
              + " ".join(f"{arm}={score[arm][0]}/{score[arm][1]}" for arm in ARMS)
              + f"  {el / n:.1f}s/q  {el / 60:.1f}min", flush=True)
    renderer.close()
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n  accuracy by gold position (n = questions x repeats; same rows every arm, order only)")
    print(f"  {'type':<24}{'n':>5}" + "".join(f"{arm:>12}" for arm in ARMS))
    types = sorted({q["question_type"] for q in picked})
    for t in types + ["ALL"]:
        cells, nn = [], 0
        for arm in ARMS:
            c, n = (score[arm] if t == "ALL" else byt[arm].get(t, [0, 0]))
            cells.append(f"{c / n:>12.3f}" if n else f"{'-':>12}")
            nn = max(nn, n)
        print(f"  {t:<24}{nn:>5}" + "".join(cells))
    spread = max(score[x][0] / max(1, score[x][1]) for x in ARMS) - min(
        score[x][0] / max(1, score[x][1]) for x in ARMS)
    print(f"\n  spread front->tail: {spread:.3f}  |  mean notes shown per block {fits / max(1, len(picked)):.1f}"
          f"  |  questions whose arms did NOT render the same token count: {tok_mismatch}")
    print(f"  read as: a spread near zero means the block is a BAG - the model samples facts out of it")
    print(f"  without regard to order, so no re-ranking or ordering feature can pay and the residual")
    print(f"  really is the reasoning path. A spread of a few points is the exact headroom any ordering")
    print(f"  feature is fighting for, and it says the front of the block is worth defending.")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
