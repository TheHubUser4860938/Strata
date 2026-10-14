#!/usr/bin/env python3
"""m4_shelves - propose NEW shelf marks from the store's own clusters (taxonomy growth by evidence).

The keyword map (tools/curate_loop.py, fact_class) shelves what it can name and leaves the rest
WILD - unclassified, riding every unfiltered request, joining no filtered one. That is the safe
home for an uncertain fact ("wrong shelves hide facts; wild shelves only wait"), but a pile of
wild rows about ONE subject is a shelf waiting to be named. Its docstring already says where
subclasses come from: "the embedder's centroids (Phase 4) or a human hand - not with more
keywords". This is that centroid pass: it clusters the rows the map could not name (or, with
--all, every row on one shelf) by cosine over the vectors the store already carries, and prints
the clusters big enough to be a subject, with their rarest shared words and exemplars.

PROPOSE ONLY. Nothing is shelved here without a human choosing the class, because the 50-group
merge audit set the law for this store: eyeball before you shelve. A false shelf HIDES a fact
from every filtered request that stops asking for it. And note what a cluster is NOT: a big tight
group whose members say the SAME thing (the 2026-10-14 run found 28 wild rows sharing
variable/environment/name/strata/user - one fact, twenty-eight phrasings) is a MERGE candidate for
tools/m2_merge.py, not a shelf candidate. Cosine cannot tell "one subject, many facts" from "one
fact, many copies"; a human reading three exemplars can, which is why the exemplars are printed.

  python3 tools/m4_shelves.py [--dir Strata-data/memory-eval] [--bar 0.62] [--min 5]
                              [--all] [--class STR] [--limit 12]

  --class STR   only cluster rows already shelved under STR (or wild rows when STR is "wild"),
                the shape a subclass proposal needs
  --apply CLASS post op=classify for the ids of the cluster(s) named by --ids, so a human decision
                lands through the API the server itself uses (its caches and credits stay right)
  --ids 12,13   the rows to shelve with --apply (the exemplars printed for a cluster)

Rows without vectors are embedded on the fly here (cheap: ~2 ms each); a store that will be
clustered often is better served by tools/m4_embed.py once.
"""
import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np                                                # noqa: E402
from serve.embedder import Embedder                               # noqa: E402
from serve.memory import norm_text, STOP                          # noqa: E402
import urllib.request as U                                        # noqa: E402
import json                                                       # noqa: E402


def rows_with_vectors(conn, emb, want_class):
    """(id, text, class, vec) for every ACTIVE row, embedded from the store when the blob is
    there and from the model when it is not. want_class: None = every row, "wild" = unclassified
    only, otherwise rows whose class starts with that prefix."""
    where = "where invalid_at is null"
    args: list = []
    if want_class == "wild":
        where += " and (class is null or class='')"
    elif want_class:
        where += " and class like ?"
        args.append(want_class + "%")
    rows = conn.execute(f"select id, text, coalesce(class,'') from memories {where} order by id",
                        args).fetchall()
    have = {r[0]: r[1] for r in conn.execute("select id, vec from embeddings").fetchall()}
    out, missing = [], []
    for rid, text, klass in rows:
        if rid in have:
            out.append((rid, text, klass, np.frombuffer(have[rid], np.float32)))
        else:
            missing.append((rid, text, klass))
    if missing:
        vecs = emb.embed_docs([m[1] for m in missing])
        out.extend((rid, text, klass, v) for (rid, text, klass), v in zip(missing, vecs))
    return out


def cluster(items, bar: float):
    """Greedy leader clustering: a row joins the nearest centroid at cosine >= bar, else it
    starts one. Order-stable (ids ascending), so two runs on the same store print the same
    clusters - the property a hand-review pass needs. The centroid is a SUM, so the comparison
    divides by its running norm (||s+v||^2 = ||s||^2 + 2 s.v + 1 for unit v): comparing the raw
    dot instead lets a cluster's own size inflate its score, and the first version of this tool
    watched all 355 wild rows pile into one group. At this store's size the O(clusters x rows)
    scan is milliseconds; no ANN index, same reasoning as the recall path."""
    groups = []                    # [ids, centroid_sum, texts, klasses, norm_sq]
    for rid, text, klass, vec in items:
        best, bcos = None, -1.0
        for g in groups:
            dot = float(g[1] @ vec)
            c = dot / np.sqrt(g[4]) if g[4] > 1e-12 else 0.0
            if c > bcos:
                best, bcos = g, c
        if best is not None and bcos >= bar:
            dot = float(best[1] @ vec)
            best[4] += 2.0 * dot + 1.0
            best[0].append(rid)
            best[1] = best[1] + vec
            best[2].append(text)
            best[3].append(klass)
        else:
            groups.append([[rid], vec.copy(), [text], [klass], 1.0])
    for g in groups:
        n = np.linalg.norm(g[1])
        if n:
            g[1] = g[1] / n
    return groups


def shared_terms(texts, df: Counter, bar: int):
    """The rarest words every member shares - the evidence a class name should be built from.
    STOP-excluded like the dedupe payload, and document-frequency filtered against the WHOLE
    store so a cluster's own connectives cannot masquerade as its subject (the airport lesson)."""
    common = None
    for t in texts:
        ts = {w for w in norm_text(t).split() if w not in STOP}
        common = ts if common is None else (common & ts)
    return sorted((w for w in (common or ())), key=lambda w: df.get(w, 0))[:8]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="Strata-data/memory-eval")
    ap.add_argument("--model", default="Strata-data/embeddings/bge-small-en-v1.5")
    ap.add_argument("--bar", type=float, default=0.62, help="cosine to join a cluster")
    ap.add_argument("--min", type=int, default=5, help="smallest cluster worth proposing")
    ap.add_argument("--all", action="store_true", help="cluster every active row, not only wild")
    ap.add_argument("--class", dest="klass", default=None,
                    help="cluster only this shelf ('wild' = unclassified)")
    ap.add_argument("--limit", type=int, default=12, help="clusters to print")
    ap.add_argument("--apply", default=None, help="class to post with --ids instead of proposing")
    ap.add_argument("--ids", default="", help="comma-separated row ids for --apply")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    a = ap.parse_args()

    db = Path(a.dir) / "memory.db"
    if not db.exists():
        print(f"no store at {db}")
        return 2
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA busy_timeout=10000")

    if a.apply:
        ids = [int(x) for x in a.ids.split(",") if x.strip()]
        if not ids:
            print("--apply CLASS needs --ids")
            return 2
        for rid in ids:
            req = U.Request(a.url + "/memory", data=json.dumps(
                {"op": "classify", "id": rid, "class": a.apply}).encode(), method="POST")
            req.add_header("Content-Type", "application/json")
            with U.urlopen(req, timeout=30) as r:
                r.read()
            print(f"  shelved {rid} -> {a.apply}")
        print(f"{len(ids)} rows classified through the API; the server's own caches stayed right")
        return 0

    want = "wild" if (a.klass == "wild" or (a.klass is None and not a.all)) else a.klass
    emb = Embedder(a.model, threads=4)
    items = rows_with_vectors(conn, emb, want)
    if not items:
        print("no rows matched")
        return 0
    df: Counter = Counter()
    for _rid, text in conn.execute(
            "select id, text from memories where invalid_at is null").fetchall():
        df.update({w for w in norm_text(text).split() if w not in STOP})
    groups = cluster(items, a.bar)
    big = [g for g in groups if len(g[0]) >= a.min]
    big.sort(key=lambda g: -len(g[0]))
    print(f"{len(items)} active rows in scope -> {len(groups)} clusters, {len(big)} of "
          f">= {a.min} rows (bar {a.bar}); {len(items) - sum(len(g[0]) for g in big)} rows sit in "
          f"small or singleton clusters, which is the honest shape of a store this size")
    for g in big[:a.limit]:
        ids, texts, klasses = g[0], g[2], g[3]
        shelves = Counter(k for k in klasses if k)
        suggest = (f"{shelves.most_common(1)[0][0]}/<new>" if shelves
                   else "<new top class>")
        print(f"\n  {len(ids):3d} rows  ids {ids[:6]}{'...' if len(ids) > 6 else ''}  "
              f"shelves here: {dict(Counter(klasses)) or 'all wild'}")
        terms = shared_terms(texts, df, a.min)
        term_txt = ", ".join(terms) if terms else (
            "(none - the cluster is semantic, not lexical: exactly what the keyword map cannot name)")
        print(f"      shared rare words: {term_txt}")
        print(f"      suggested mark: {suggest}   (a human names it; nothing is shelved by this tool)")
        for t in texts[:3]:
            print(f"        | {t[:88]}")
    print("\nshelve one with:  tools/m4_shelves.py --apply CLASS --ids <ids>   (proposals are "
          "evidence, not decisions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
