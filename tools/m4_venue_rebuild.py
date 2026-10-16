#!/usr/bin/env python3
"""m4_venue_rebuild - the venue store back offline: no server boot, no engine, no second instance.

The venue store (the LongMemEval haystacks, ~11k verbatim rows in lme_<qid> scopes) lives in /tmp
and dies with every reboot; rebuilding it used to mean a venue-day boot of the dev server - a
process the no-second-instance rule forbids spending on a benchmark cache. This tool runs the SAME
ingest laws as lme_harness.ingest, IN-PROCESS against a fresh store dir: same day prefix (the
day_of fix of 2026-10-15 makes it real), same assistant-role lead, same chunk_turn caps with gold
riding every piece, same guard=False evidence law (a haystack is evidence, not proposals - the
semantic guard must not move the gold), same first-session/OR-of-gold map merge, same sidecar
format - so a retrieval sweep (m4_blockrule, lme_retrieval_ab --offline, a rerank probe) sees
exactly the store the venue would have served.

Embedding rides the write (the hybrid arm needs it): the filed ~40 min for 11k rows on the CPU
embedder is the cost, paid once per reboot, in the background, while the live server works.
RESUMABLE: scopes already ingested are skipped (the map is the resume journal - a question lands
in the map only after all its rows are in), so an interrupted rebuild continues where it stopped.

    python3 tools/m4_venue_rebuild.py [--dir /tmp/opencode/venue]
        [--data /tmp/opencode/longmemeval/data/longmemeval_oracle.json]
        [--map DIR/venue.map] [--limit N] [--model Strata-data/embeddings/bge-small-en-v1.5]
"""
from __future__ import annotations

import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import lme_harness as H                                       # noqa: E402  day_of, chunk_turn - the laws
from serve.memory import MemoryService                        # noqa: E402
from serve.embedder import Embedder                           # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="/tmp/opencode/venue")
    ap.add_argument("--data", default="/tmp/opencode/longmemeval/data/longmemeval_oracle.json")
    ap.add_argument("--map", dest="map_path", default="")
    ap.add_argument("--model", default=str(ROOT / "Strata-data/embeddings/bge-small-en-v1.5"))
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="first N questions only (smoke)")
    a = ap.parse_args()
    map_path = Path(a.map_path) if a.map_path else Path(a.dir) / "venue.map"
    Path(a.dir).mkdir(parents=True, exist_ok=True)

    done = set()
    try:
        for line in open(map_path):
            done.add(json.loads(line)["question_id"])
    except FileNotFoundError:
        pass

    svc = MemoryService(a.dir, embedder=Embedder(a.model, a.threads))
    data = json.load(open(a.data))
    if a.limit:
        data = data[:a.limit]
    todo = [q for q in data if q["question_id"] not in done]
    print(f"[venue] {len(done)} questions already ingested, {len(todo)} to go; "
          f"store {a.dir}", flush=True)
    t0, rows_now = time.time(), svc.conn.execute(
        "select count(*) from memories where invalid_at is null").fetchone()[0]
    with open(map_path, "a") as mapf:
        for qi, q in enumerate(todo, 1):
            qid, scope = q["question_id"], f"lme_{q['question_id']}"
            where: dict[int, tuple[bool, str]] = {}
            for si, session in enumerate(q["haystack_sessions"]):
                sid = q["haystack_session_ids"][si]
                day = H.day_of(q["haystack_dates"][si])
                for turn in session:
                    text = turn.get("content") or ""
                    if not text.strip():
                        continue
                    lead = f"[{day}] " if day else ""
                    if turn.get("role") != "user":
                        lead += "The assistant told the user: "
                    for piece in H.chunk_turn(lead + text):
                        r = svc.remember(piece, scope=scope, source="ingest-2026-10-16", guard=False)
                        if r.get("deduped") == "semantic":
                            raise RuntimeError(                       # same law as H.ingest: the map
                                f"{qid}: guard=False did not stand aside, gold would shuffle")
                        rid = r.get("id")
                        if rid is not None:
                            prev = where.get(rid)
                            gold = bool(turn.get("has_answer")) or bool(prev and prev[0])
                            where[rid] = (gold, prev[1] if prev else sid)
            mapf.write(json.dumps({"question_id": qid,
                                   "where": {str(k): [v[0], v[1]] for k, v in where.items()}}) + "\n")
            mapf.flush()
            if qi % 25 == 0 or qi == len(todo):
                n = svc.conn.execute("select count(*) from memories where invalid_at is null").fetchone()[0]
                rate = (n - rows_now) / max(1.0, time.time() - t0)
                print(f"[venue] {len(done) + qi}/{len(data)} questions | {n} rows | {rate:.1f} rows/s "
                      f"| eta {(len(todo) - qi) * 22 / max(1, qi):.0f} min", flush=True)
    svc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
