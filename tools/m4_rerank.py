#!/usr/bin/env python3
"""m4_rerank - the venue's rows came from dated sessions; does ORDERING the block by session date help?

The grounding A/B (f588765b) closed the writer-side route: the dated prompt put ISO dates on 1.1% of
extracted facts and the answer-level delta was -0.008 - the noise itself. The mutation probe (0.808
flip) says the present gold row carries the answer, and the sensitivity probe (+0.085) says MORE
presence doesn't help - the failure is the ordering OPERATION. This is the presentation half of that:
same rows, same retrieval, same budget, two block ORDERS.

One archaeology note, measured here rather than assumed: the venue rows carry NO date prefix
(0 of 10,973 match `[YYYY-MM-DD]`) - the retained oracle store predates the harness's date-prefixed
ingest (06b58434, 2026-10-13), which every re-ingest since would have written. So the dates come from
the gold map instead: row-id -> session-id (the sidecar every probe already trusts) -> that session's
haystack date. The store never carried the benchmark's dates; the sidecar always did.

Two arms per question, in-process from the embedded venue copy, memory muted, the shipped shape
(block in the system head, question from the user, temp 0.8 / 500 tok, official checker judge):

  default   the block exactly as recall ranks it (the product's order: RRF fusion)
  chrono    the SAME hits re-sorted ASCENDING by their session's date (stable, so same-date rows
            keep recall rank); rows the map cannot place sort as "0000-00-00", i.e. first - the
            story starts with what cannot be placed

The dates come from the gold-map sidecar (row -> session -> session day), not the rows, and
_blocks is cleared per question - the pin-cache law (pin hits return id-only dicts, and the budget
is not in the key) is what has produced wrong headlines before.

CALIBRATION THIS TOOL ALREADY BANKED (its first run, same evening, before the archaeology): the
rows' missing prefixes made both arms sort to the same order, so both rendered BYTE-IDENTICAL
blocks - and the paired result, 0.425 vs 0.472 with 14:8 discordants (22 of 127 lines flipped),
IS the test-retest noise floor of this pool at temp 0.8 (record:
spikes/lme/rerank-noise-floor-20261015.jsonl). A single-run delta under ~0.05 on 127 lines is
inside that floor - the >=3-repeat rule, finally with a number on it.

Falsifier, stated before the run: if the paired delta (chrono-right-only minus default-right-only)
is inside the discordant noise, then block ORDER is not the lever either, and the temporal deficit is
confirmed as the model's ordering operation itself - a presentation fix cannot reach it and the queue
moves to the extraction-path finding (the -7.1-point, 45%-idk cost of k=2 facts, f588765b) and the
arm C1 window. A delta clearing the noise makes chronological rendering a candidate rule - opt-in
first, and the >=3-repeat protocol before any default changes.

Pool: the same 127 temporal questions as m4_grounding, so the three runs form one ladder:
verbatim-default 0.457 (filed) / verbatim-chrono ? / extracted 0.386-0.378 (measured). The filed run
is the external anchor; the DEFAULT arm here is the paired control (in-process shape, fidelity
0.867 against the filed run per m4_mutation).

    python3 tools/m4_rerank.py                 # full 127, both arms
    python3 tools/m4_rerank.py --limit 3       # smoke
"""
from __future__ import annotations

import argparse, collections, json, sys, time
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
    ap.add_argument("--map", dest="mpath", default="/tmp/opencode/lme_oracle_full.jsonl.map",
                    help="row-id -> (has_answer, session_id); the dates come from here, not the rows")
    ap.add_argument("--store", default="/tmp/opencode/venue-ab-store/hyb")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--types", default="temporal-reasoning")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="/tmp/opencode/rerank-temporal.jsonl")
    a = ap.parse_args()

    data = {q["question_id"]: q for q in json.load(open(a.data))}
    maps = H.load_map(a.mpath)
    recs = [json.loads(l) for l in open(Path(ROOT / a.records) if not Path(a.records).is_absolute()
                                        else a.records)]
    pool = [r for r in recs if r.get("type") in set(a.types.split(",")) and r["question_id"] in data]
    if a.limit:
        pool = pool[:a.limit]
    print(f"{len(pool)} questions, 2 arms (default rank vs chronological) = {2*len(pool)} answers "
          f"+ {2*len(pool)} judge calls", flush=True)

    m = MemoryService(a.store, recall_limit=192, max_block_chars=48000, encrypt=True,
                      key_file=str(Path(a.store) / "memory.key"),
                      embedder=__import__("serve.embedder", fromlist=["Embedder"]).Embedder(
                          str(ROOT / "Strata-data/embeddings/bge-small-en-v1.5"), threads=4))

    tally = collections.Counter()
    out = open(a.out, "w")
    for i, r in enumerate(pool, 1):
        q, qid = data[r["question_id"]], r["question_id"]
        scope = f"lme_{qid}"
        m._blocks.clear()
        _b, shown, _qq = m.block_for([{"role": "user", "content": q["question"]}], scope, semantic=True)
        if not shown:
            tally["no_block"] += 1
            continue
        ids = [h["id"] for h in shown]
        # dates from the sidecar the probes already trust: row -> session -> that session's day.
        # (The rows themselves carry no prefix - 0 of 10,973 - the store predates the dated ingest.)
        where = maps.get(qid) or {}
        sid2day = {q["haystack_session_ids"][i]: H.day_of(q["haystack_dates"][i])
                   for i in range(len(q["haystack_sessions"]))}
        dts = {}
        for hid in ids:
            dts[hid] = sid2day.get(where.get(hid, (None, None))[1] or "", "") or "0000-00-00"
        # stable sort: same day keeps recall rank; unplaceable rows lead the story as "0000-00-00"
        chrono = sorted(ids, key=dts.get)
        dated = sum(1 for hid in ids if dts[hid] != "0000-00-00")
        arms = {}
        for name, order in (("default", ids), ("chrono", chrono)):
            by_id = {h["id"]: h for h in shown}
            blk, _ = m._render_block([by_id[hid] for hid in order], m.max_block_chars, scope)
            msgs = [{"role": "system", "content": blk},
                    {"role": "user", "content": f"{q['question']}\n\n{H.ANSWER_RULES['con']}"}]
            try:
                resp = H.post(a.url, "/v1/chat/completions",
                              {"model": "", "messages": msgs, "max_tokens": 500, "temperature": 0.8,
                               "reasoning_effort": "off"},
                              headers={"X-Strata-Memory": "off"}, timeout=300.0)
                text = (resp["choices"][0]["message"].get("content") or "").strip()
            except Exception:                                       # noqa: BLE001
                text = ""
            empty = text == ""
            arms[name] = {"answer": text[:300], "empty": empty,
                          "right": H.judge(a.url, q, text) if not empty else False,
                          "idk": "idk" in text.strip().lower().split("\n")[-1]}
        d, c = arms["default"], arms["chrono"]
        tally["n"] += 1
        tally["default_right"] += d["right"]
        tally["chrono_right"] += c["right"]
        tally["b_chrono_only"] += (c["right"] and not d["right"])
        tally["c_default_only"] += (d["right"] and not c["right"])
        tally["fidelity_vs_filed"] += (d["right"] == bool(r.get("correct")))
        tally["empty"] += d["empty"] + c["empty"]
        out.write(json.dumps({"question_id": qid, "notes": len(ids), "dated_rows": dated,
                              "filed_correct": bool(r.get("correct")), "arms": arms},
                             ensure_ascii=False) + "\n")
        print(f"  {i:>3}/{len(pool)} {qid[:14]} rows {len(ids):>3} dated {dated:>3}  "
              f"default {'R' if d['right'] else 'w'}{' (idk)' if d['idk'] else ''}  "
              f"chrono {'R' if c['right'] else 'w'}{' (idk)' if c['idk'] else ''}", flush=True)
    m.close()

    N = max(1, tally["n"])
    print(f"\n  === {tally['n']} questions x 2 arms ({tally['no_block']} skipped, "
          f"{tally['empty']} EMPTY completions - each one is a probe failure, never a wrong answer) ===")
    print(f"  default-rank accuracy {tally['default_right'] / N:.3f}   chrono accuracy "
          f"{tally['chrono_right'] / N:.3f}")
    print(f"  paired: chrono-right-only {tally['b_chrono_only']}  default-right-only "
          f"{tally['c_default_only']}  delta {(tally['b_chrono_only'] - tally['c_default_only']) / N:+.3f}")
    print(f"  fidelity: default arm agrees with the filed run on {tally['fidelity_vs_filed']}/{N} lines")
    print(f"\n  read as: the discordants are the noise floor; a delta inside them says block ORDER is "
          f"not the lever\n  either, and the temporal deficit is the model's ordering operation, not "
          f"the presentation. A delta\n  outside them prices a chronological-render rule (opt-in first, "
          f">=3 repeats before a default).")
    print(f"  full record: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
