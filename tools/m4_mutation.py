#!/usr/bin/env python3
"""m4_mutation - does the answer actually come from the block, or does the block just sit there?

`sem` refuses a claim until something makes it fail ("prove every promise can fail: each needs a
mutation", github.com/Ataraxy-Labs/sem). The claim this prices is the one the whole venue queue is
built on - rendered-block coverage predicts accuracy - and m4_sensitivity.py measured its observational
half on 2026-10-15: full gold coverage beats incomplete by +0.191 overall, but only **+0.085 for
temporal-reasoning** and **+0.000 for knowledge-update**. A within-run correlation is still not a
mutation. This is.

Three arms per line, all with memory MUTED and the block supplied by this process, so the only thing
that differs between them is the block's content:

  control     the block as the store renders it for that question's scope
  drop-gold   the same block with the gold rows' lines removed and re-rendered (nothing else touched)
  no-block    the question alone - the model's own weights, no memory at all

If `control` is right and `drop-gold` is wrong, the block carried the answer and the answer is the
block's. If `drop-gold` stays right, the answer came from somewhere else: another line in the block
(the store holds near-duplicates of most facts, and m4_shelfcall measured exactly that collapse), or
the model's weights. The `no-block` arm separates those two cases, which is why it is not optional.

Selection defaults to the pool that matters: temporal-reasoning lines that were CORRECT at FULL gold
coverage - the 372-line pool where the evidence was complete and the model still failed 20% of the
time, and the pool the date-grounding arm is meant to fix.

Falsifier, stated before the run: if the drop-gold flip rate is under 0.30, then for temporal
questions the gold line in the block is not what produces the answer, and adding absolute dates to
that line will not either - the grounding ingest would be buying something the metric cannot cash.
Flip rate above ~0.60 says the block is load-bearing and the arm is worth its hours.

Protocol, same as every venue measurement here: answers at temperature 0.8 (never 0), max_tokens 500,
one fresh conversation per ask, the judge is the official LongMemEval checker prompt run locally
(temp 0, 10 tokens - the no-zero rule governs the answer under measurement, not the scorer). One
caveat stated plainly: the block is injected by this process in a fixed shape, so the CONTROL arm's
absolute accuracy is a fidelity check on this probe, not a product claim - the product path was
measured by lme_harness (0.760 on 2026-10-15). What this probe prices is the DIFFERENCE between arms,
and all three arms share the same shape.

    python3 tools/m4_mutation.py --n 30 --types temporal-reasoning
"""
from __future__ import annotations

import argparse, collections, json, random, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import lme_harness as H                                          # noqa: E402
from serve.memory import MemoryService                           # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--records", default="spikes/lme/armc-20261015-1rep.jsonl")
    ap.add_argument("--data", default="/tmp/opencode/longmemeval/data/longmemeval_oracle.json")
    ap.add_argument("--map", dest="mpath", default="/tmp/opencode/lme_oracle_full.jsonl.map")
    ap.add_argument("--store", default="/tmp/opencode/venue-ab-store/hyb",
                    help="the embedded venue copy (the same one m4_blockcov measures coverage on)")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--n", type=int, default=30, help="lines to probe (deterministic sample)")
    ap.add_argument("--types", default="temporal-reasoning")
    ap.add_argument("--need-full-cov", action="store_true", default=True,
                    help="restrict to lines whose gold coverage was complete (the default)")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", default="/tmp/opencode/mutation-probe.jsonl")
    a = ap.parse_args()

    data = {q["question_id"]: q for q in json.load(open(a.data))}
    maps = H.load_map(a.mpath)
    recs = [json.loads(l) for l in open(a.records)]
    types = set(a.types.split(",")) if a.types else set()
    pool = [r for r in recs
            if (not types or r.get("type") in types)
            and r.get("correct")
            and (not a.need_full_cov or (r.get("row_coverage") or 0) >= 0.999)
            and r["question_id"] in data]
    rng = random.Random(a.seed)
    sel = rng.sample(pool, min(a.n, len(pool)))
    print(f"pool {len(pool)} lines (correct, full gold coverage, type in {types or 'all'}) "
          f"-> probing {len(sel)}")
    if not sel:
        print("empty pool - nothing to mutate")
        return 2

    m = MemoryService(a.store, recall_limit=192, max_block_chars=48000, encrypt=True,
                      key_file=str(Path(a.store) / "memory.key"),
                      embedder=__import__("serve.embedder", fromlist=["Embedder"]).Embedder(
                          str(ROOT / "Strata-data/embeddings/bge-small-en-v1.5"), threads=4))

    tally = collections.Counter()
    out = open(a.out, "w")
    for i, r in enumerate(sel, 1):
        q, qid = data[r["question_id"]], r["question_id"]
        scope = f"lme_{qid}"
        where = maps.get(qid) or {}
        gold = {rid for rid, (ha, _s) in where.items() if ha}
        m._blocks.clear()                       # one line, one fresh pin (the budget is not in the key)
        _b, shown, _q = m.block_for([{"role": "user", "content": q["question"]}], scope, semantic=True)
        ids = [h["id"] for h in shown]
        hit_gold = [h for h in shown if h["id"] in gold]
        if not shown or not hit_gold:
            tally["skipped_no_block_or_no_gold"] += 1
            continue
        keep = [h for h in shown if h["id"] not in gold]
        arms = {}
        for name, hits in (("control", shown), ("drop-gold", keep), ("no-block", [])):
            # The product's own shape: the memory block rides the SYSTEM head and the question comes
            # from the user. A system-only conversation returns an empty completion from this chat
            # template - measured on the first smoke of this tool, three lines, three empty answers -
            # which would have read as "the block does not matter" if the fidelity arm had not been there.
            if hits:
                blk, _ = m._render_block(hits, m.max_block_chars, scope)
                msgs = [{"role": "system", "content": blk},
                        {"role": "user", "content": f"{q['question']}\n\n{H.ANSWER_RULES['con']}"}]
            else:
                msgs = [{"role": "user", "content": f"{q['question']}\n\n{H.ANSWER_RULES['con']}"}]
            try:
                hyp = H.post(a.url, "/v1/chat/completions",
                             {"model": "", "messages": msgs,
                              "max_tokens": 500, "temperature": 0.8, "reasoning_effort": "off"},
                             headers={"X-Strata-Memory": "off"}, timeout=300.0)
                text = (hyp["choices"][0]["message"].get("content") or "").strip()
            except Exception as e:                                        # noqa: BLE001
                print(f"  {i} {qid}: call failed: {e}")
                text = ""
            if not text:
                # an empty completion is a PROBE failure, never a wrong answer: without this the
                # fidelity check below would silently read as "the block does not matter"
                tally["empty_answers"] += 1
            arms[name] = {"answer": text, "right": H.judge(a.url, q, text),
                          "idk": "idk" in text.strip().lower().split("\n")[-1]}
        c, d, n = arms["control"], arms["drop-gold"], arms["no-block"]
        tally["n"] += 1
        tally["control_right"] += c["right"]
        tally["dropgold_right"] += d["right"]
        tally["noblock_right"] += n["right"]
        if c["right"] and not d["right"]:
            tally["FLIP"] += 1
        if c["right"] and d["right"] and not n["right"]:
            tally["block_load_bearing"] += 1        # only the block knows it
        if c["right"] and d["right"] and n["right"]:
            tally["weights_or_twin"] += 1           # survived dropping the gold line entirely
        out.write(json.dumps({"question_id": qid, "type": r.get("type"),
                              "notes": len(ids), "gold_rows": len(gold), "gold_in_block": len(hit_gold),
                              "arms": arms}, ensure_ascii=False) + "\n")
        print(f"  {i:>3}/{len(sel)} {qid[:14]} gold-in-block {len(hit_gold)}/{len(gold)}  "
              f"control {'R' if c['right'] else 'w'}{' (idk)' if c['idk'] else ''}  "
              f"drop-gold {'R' if d['right'] else 'w'}{' (idk)' if d['idk'] else ''}  "
              f"no-block {'R' if n['right'] else 'w'}", flush=True)
    m.close()

    N = max(1, tally["n"])
    print(f"\n  === {tally['n']} lines probed ({tally['skipped_no_block_or_no_gold']} skipped) ===")
    for name in ("control", "drop-gold", "no-block"):
        k = {"control": "control_right", "drop-gold": "dropgold_right", "no-block": "noblock_right"}[name]
        print(f"  {name:<12} accuracy {tally[k] / N:>6.3f}")
    # two denominators, because they answer different questions: over every probed line it is the
    # arm-level rate, over control-right lines only it is the conditional the arm decision rests on
    # (a line the control got wrong cannot flip). The 4-of-30 control misses are temp-0.8 repeat
    # variance, which is why the protocol asks for >=3 repeats before any of this becomes a claim.
    cr = max(1, tally["control_right"])
    print(f"  FLIP RATE (control right -> dropping the gold line makes it wrong): "
          f"{tally['FLIP'] / N:>6.3f} of probed lines ({tally['FLIP']}/{tally['n']})   "
          f"{tally['FLIP'] / cr:.3f} of the lines the control actually got right "
          f"({tally['FLIP']}/{cr})")
    if tally["empty_answers"]:
        print(f"  EMPTY completions: {tally['empty_answers']} of {3 * N} calls - the probe is not measuring "
              f"what it claims")
    print(f"  fidelity: control reproduces the filed run on {tally['control_right']}/{N} lines "
          f"(these were all CORRECT there)")
    print(f"  block-only (right with the block, wrong without any block):      "
          f"{tally['block_load_bearing'] / N:>6.3f}")
    print(f"  survived the drop and the cold arm too (weights or a twin line): "
          f"{tally['weights_or_twin'] / N:>6.3f}")
    print(f"\n  read as: the flip rate is the mutation sem demands before it accepts a claim. High flip "
          f"means\n  the gold line is load-bearing and date-grounding has something to improve. Low flip "
          f"means the\n  answer is coming from elsewhere - a twin line or the model's own weights - and an "
          f"ingest that\n  rewrites those lines with dates is buying something this metric cannot cash.")
    print(f"  full record: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
