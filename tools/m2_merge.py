"""M2 consolidation: merge rephrased duplicate memories by CONTENT ADDRESS (v2: relative
rare-token bar + payload INTERSECTION grouping - see groups()).

The store dedups by exact norm_text on write, but the curation loop re-derives facts in
different phrasings (the eval corpus is template-shaped: "KXXX is licensed to YYY" and the
question that asks it share every boilerplate token).  Token similarity cannot tell those
apart, so this tool hashes the PAYLOAD: the rare tokens (document frequency <= max(3, rows//50)
- relative, because duplication itself makes a copy's payload common) of each fact's normalized
text.  Two facts whose payloads share >= 2 rare tokens (or half the smaller payload) inside one
scope are the same fact said differently; the boilerplate is noise.

Survivor: a declarative beats a question (a question stored as a fact injects no answer),
then the most informative text, then the oldest row.  Redundant rows are retired over the API
with op=invalidate (the Zep pattern: out of recall, kept as version history pointing at the
survivor; --hard uses op=forget and deletes).  Negation-mismatched groups are reported, never
merged: "X is Y" vs "X is not Y" share a payload and mean opposite things.

Dry run by default; --apply deletes.  Prints before/after counts.  No engine calls.
"""
import argparse
import json
import sqlite3
import sys
import urllib.request
from collections import Counter, defaultdict

sys.path.insert(0, __file__.rsplit("/", 2)[0])
from serve.memory import STOP, norm_text  # noqa: E402  (same identity + same stop-words the store dedups on)

NEG = {"not", "no", "never", "n't", "none", "without", "isnt", "arent"}
WH = ("who", "what", "when", "where", "which", "why", "how", "is", "are", "was",
     "does", "did", "do", "in", "on", "at", "can", "has", "have", "name", "list")


def declarative(text: str) -> bool:
    t = text.strip()
    return not t.endswith("?") and t.split(" ", 1)[0].lower().strip(",.;:") not in WH


def payload_of(tokens, df, bar):
    return frozenset(t for t in tokens if t not in STOP and df[t] <= bar)


def groups(rows, df, bar):
    """Group by payload CONTENT (v2), three rules any one fires: payloads EQUAL (v1's rule, still
    the cleanest signal), share >= 4 rare tokens (paraphrase clusters), or share >= 2 rare tokens
    AND at least 70% of the smaller payload (short facts: "KTNC operates from Falls City" beside
    "KTNC broadcasts from Falls City"). Two hard lessons from the live store (2026-10-12): the bar
    is RELATIVE, max(3, rows // 50) - when one fact exists in five copies its payload tokens stop
    being rare, and v1's absolute df<=3 made the five-phrasing Qwen3.8 cluster invisible; and
    STOP words never count as payload - in a small store "the" looks rare, and two DIFFERENT facts
    about one subject must not group on grammar. Union-find merges the transitive matches;
    negation-mismatched groups stay reported-never-merged, and the dry-run + hand-review before
    --apply stays law (the 50-group audit caught what blind applying would not have)."""
    docs = [set(norm_text(r["text"]).split()) for r in rows]
    pays = [payload_of(d, df, bar) for d in docs]
    negs = [bool(d & NEG) for d in docs]
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def match(a, b):
        if a == b:
            return True
        inter = len(a & b)
        return inter >= 4 or (inter >= 2 and inter >= 0.7 * min(len(a), len(b)))

    for i in range(len(rows)):
        if not pays[i]:
            continue
        for j in range(i + 1, len(rows)):
            if not pays[j] or rows[j]["scope"] != rows[i]["scope"]:
                continue
            if match(pays[i], pays[j]):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    by = defaultdict(list)
    for i in range(len(rows)):
        if pays[i]:
            by[(rows[i]["scope"], find(i))].append(i)
    out, skipped = [], 0
    for key, idx in by.items():
        if len(idx) < 2:
            continue
        if len({negs[i] for i in idx}) > 1:
            skipped += 1
            continue
        out.append((key, idx))
    return out, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="Strata-data/memory-eval/memory.db")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--apply", action="store_true", help="retire the redundant rows (default: dry run)")
    ap.add_argument("--hard", action="store_true", help="with --apply: delete (op=forget) instead of "
                                                        "invalidate - history gone, not just out of recall")
    ap.add_argument("--show", type=int, default=6, help="sample groups to print")
    a = ap.parse_args()

    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("pragma table_info(memories)")}   # pre-job-7 dbs have no invalid_at
    live = "where invalid_at is null" if "invalid_at" in cols else ""
    rows = [dict(r) for r in conn.execute(
        f"select id,scope,text,source,hits,confidence,created_at from memories {live} order by id")]
    docs = [set(norm_text(r["text"]).split()) for r in rows]
    df = Counter(t for d in docs for t in d)
    bar = max(3, len(rows) // 50)          # the RELATIVE rare-token bar (see groups())

    gr, skipped = groups(rows, df, bar)
    drop, keep_ids = [], []
    for key, idx in sorted(gr, key=lambda kv: (kv[0][0], min(kv[1]))):
        best = sorted(idx, key=lambda i: (declarative(rows[i]["text"]),
                                          len(norm_text(rows[i]["text"])), -rows[i]["created_at"]))[-1]
        keep_ids.append(rows[best]["id"])
        for i in idx:
            if i != best:
                drop.append((rows[i]["id"], rows[best]["id"], rows[i]["text"], rows[best]["text"]))

    print(f"memories: {len(rows)}  rare-token bar df<={bar}  payload groups: {len(gr)}  mergeable: {len(drop)}"
          f"  negation-skipped: {skipped}")
    for _, _, loser, winner in drop[:a.show]:
        print(f"  keep {winner[:60]!r}\n  drop {loser[:60]!r}")
    if not a.apply:
        print("dry run - add --apply to retire the redundant rows (or --hard to delete them)")
        return

    n = 0
    op, okkey = ("forget", "forgotten") if a.hard else ("invalidate", "invalidated")
    for loser_id, winner_id, _, _ in drop:
        body = {"op": op, "id": loser_id}
        if op == "invalidate":
            body["by"] = winner_id
        req = urllib.request.Request(a.url.rstrip("/") + "/memory", method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=30))
        n += 1 if r.get(okkey) else 0
    stats = json.load(urllib.request.urlopen(a.url.rstrip("/") + "/memory", timeout=30))["stats"]
    print(f"applied: {n} forgotten, store now {stats['memories']} (server stats, not a direct db read)")


if __name__ == "__main__":
    main()
