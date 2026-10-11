"""M2 consolidation: merge rephrased duplicate memories by CONTENT ADDRESS.

The store dedups by exact norm_text on write, but the curation loop re-derives facts in
different phrasings (the eval corpus is template-shaped: "KXXX is licensed to YYY" and the
question that asks it share every boilerplate token).  Token similarity cannot tell those
apart, so this tool hashes the PAYLOAD: the rare tokens (document frequency <= 3) of each
fact's normalized text.  Two facts with the same payload inside one scope are the same fact
said differently; the boilerplate is noise.

Survivor: a declarative beats a question (a question stored as a fact injects no answer),
then the most informative text, then the oldest row.  Redundant rows are forgotten over the
API (op=forget) so the server sees the change; a version bump follows every delete.
Negation-mismatched groups are reported, never merged: "X is Y" vs "X is not Y" share a
payload and mean opposite things.

Dry run by default; --apply deletes.  Prints before/after counts.  No engine calls.
"""
import argparse
import json
import sqlite3
import sys
import urllib.request
from collections import Counter, defaultdict

sys.path.insert(0, __file__.rsplit("/", 2)[0])
from serve.memory import norm_text  # noqa: E402  (same identity the store dedups on)

NEG = {"not", "no", "never", "n't", "none", "without", "isnt", "arent"}
WH = ("who", "what", "when", "where", "which", "why", "how", "is", "are", "was",
     "does", "did", "do", "in", "on", "at", "can", "has", "have", "name", "list")


def declarative(text: str) -> bool:
    t = text.strip()
    return not t.endswith("?") and t.split(" ", 1)[0].lower().strip(",.;:") not in WH


def payload_of(tokens, df):
    return frozenset(t for t in tokens if df[t] <= 3)


def groups(rows, df):
    docs = [set(norm_text(r["text"]).split()) for r in rows]
    negs = [bool(d & NEG) for d in docs]
    by = defaultdict(list)
    for i, r in enumerate(rows):
        p = payload_of(docs[i], df)
        if p:
            by[(r["scope"], p)].append(i)
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
    ap.add_argument("--apply", action="store_true", help="forget the redundant rows (default: dry run)")
    ap.add_argument("--show", type=int, default=6, help="sample groups to print")
    a = ap.parse_args()

    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "select id,scope,text,source,hits,confidence,created_at from memories order by id")]
    docs = [set(norm_text(r["text"]).split()) for r in rows]
    df = Counter(t for d in docs for t in d)

    gr, skipped = groups(rows, df)
    drop, keep_ids = [], []
    for key, idx in sorted(gr, key=lambda kv: (kv[0][0], min(kv[1]))):
        best = sorted(idx, key=lambda i: (declarative(rows[i]["text"]),
                                          len(norm_text(rows[i]["text"])), -rows[i]["created_at"]))[-1]
        keep_ids.append(rows[best]["id"])
        for i in idx:
            if i != best:
                drop.append((rows[i]["id"], rows[best]["id"], rows[i]["text"], rows[best]["text"]))

    print(f"memories: {len(rows)}  payload groups: {len(gr)}  mergeable: {len(drop)}"
          f"  negation-skipped: {skipped}")
    for _, _, loser, winner in drop[:a.show]:
        print(f"  keep {winner[:60]!r}\n  drop {loser[:60]!r}")
    if not a.apply:
        print("dry run - add --apply to forget the redundant rows")
        return

    n = 0
    for loser_id, _, _, _ in drop:
        req = urllib.request.Request(a.url.rstrip("/") + "/memory", method="POST",
                                     data=json.dumps({"op": "forget", "id": loser_id}).encode(),
                                     headers={"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=30))
        n += 1 if r.get("forgotten") else 0
    stats = json.load(urllib.request.urlopen(a.url.rstrip("/") + "/memory", timeout=30))["stats"]
    print(f"applied: {n} forgotten, store now {stats['memories']} (server stats, not a direct db read)")


if __name__ == "__main__":
    main()
