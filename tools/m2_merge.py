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
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, __file__.rsplit("/", 2)[0])
from serve.memory import STOP, attach, norm_text  # noqa: E402  (same identity + same stop-words the store dedups on)

NEG = {"not", "no", "never", "n't", "none", "without", "isnt", "arent"}
WH = ("who", "what", "when", "where", "which", "why", "how", "is", "are", "was",
     "does", "did", "do", "in", "on", "at", "can", "has", "have", "name", "list")
META_V = ("identified", "asked", "requested", "refers", "referenced", "instructed", "tracking",
          "provided", "noted", "stated", "plans", "intending", "intends", "decided", "shared",
          "gave", "discussed", "observed", "discovered", "interest", "interested", "working",
          "likely", "saved")


def declarative(text: str) -> bool:
    t = text.strip()
    return not t.endswith("?") and t.split(" ", 1)[0].lower().strip(",.;:") not in WH


def meta(text: str) -> bool:
    """A fact ABOUT the conversation rather than IN it: "The user identified that X", "The
    assistant found defect Y". Measured 2026-10-12, the merge review: these share payload tokens
    with the facts they discuss (the taxonomy-discussion row swallowed the Blackberry/Itasca,
    Darnen/Stevens and Tynsid/Polk township facts whole) and they are usually LONGER, so the
    survivor rule kept the commentary and dropped the content - exactly backwards. The survivor
    now prefers the fact over the conversation about the fact."""
    w = norm_text(text).split()[:5]
    return ("user" in w or "assistant" in w) and any(m in w for m in META_V)


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
        if inter >= 4:
            return True
        # the 70% path only counts when the SMALLER payload has real content (>= 4 tokens):
        # on 2026-10-13 (round 3, 2,424 rows) two shared mid-frequency words (projects, directory)
        # bridged the project-LOCATION fact to six TEST-SUITE rows - 2/2 = 100% of a 2-token
        # payload is a grammar bridge, not content
        return inter >= 2 and min(len(a), len(b)) >= 4 and inter >= 0.7 * min(len(a), len(b))

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
    ap.add_argument("--db", default="Strata-data/memory-eval",
                    help="store DIRECTORY (ADR-002: the cluster lives inside it; a path ending in "
                         "memory.db still resolves to its directory, for old hands' sake)")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--apply", action="store_true", help="retire the redundant rows (default: dry run)")
    ap.add_argument("--hard", action="store_true", help="with --apply: delete (op=forget) instead of "
                                                        "invalidate - history gone, not just out of recall")
    ap.add_argument("--show", type=int, default=6, help="sample groups to print")
    ap.add_argument("--exclude", default="", help="comma-separated loser ids to skip (the hand-"
                                                  "review veto: value-mismatch collisions like B390 "
                                                  "vs G3 share a predicate but are different facts)")
    ap.add_argument("--only", default="", help="comma-separated row ids: run the law over THIS set "
                                               "only (the shelf-cluster review in "
                                               "tools/m4_shelves.py). Rarity and the rare-token bar "
                                               "stay store-wide; only these rows may group.")
    a = ap.parse_args()

    # ADR-002: the store is Postgres - attach() is the tools' one-liner twin of the old
    # sqlite3.connect, and the attach law (measured, R0b) keeps a dry run safe beside a live
    # server. The pre-job-7 pragma probe retires with SQLite: a PG store carries invalid_at
    # from birth, so "active rows only" needs no capability check. created_at crosses as an
    # epoch float (extract) so the survivor sort -rows[i]["created_at"] keeps its exact law.
    from psycopg.rows import dict_row
    _p = Path(a.db)
    store = str(_p.parent if _p.name.startswith("memory.db") else _p)
    conn = attach(store, autocommit=True, row_factory=dict_row)
    rows = [dict(r) for r in conn.execute(
        "select id,scope,text,source,hits,confidence,extract(epoch from created_at) as created_at"
        " from memories where invalid_at is null order by id").fetchall()]
    conn.close()
    docs = [set(norm_text(r["text"]).split()) for r in rows]
    df = Counter(t for d in docs for t in d)
    bar = max(3, len(rows) // 50)          # the RELATIVE rare-token bar (see groups())
    # rare-name SUBJECTS: a token with df<=3 is a name (one township, one person, one callsign).
    # Round-2's 44% veto rate and round-3's 94 data-losses all had the same shape: two rows whose
    # grammar rhymed because shared mid-frequency words (minnesota, located, county) cleared the
    # relative bar, while each named a subject the other never mentioned. The subject rule below
    # retires a loser only when the winner can carry everything the loser uniquely says.
    uniq = [{t for t in d if df[t] <= 3} for d in docs]
    pays = [payload_of(d, df, bar) for d in docs]   # same payload the grouping matched on

    if a.only:
        # A targeted pass. The merge law runs UNCHANGED - grouping, survivor rule, subject and
        # containment vetoes - but only the named rows are allowed to form a group. Rarity stays
        # measured over the WHOLE store, because a payload's rare tokens are rare because of
        # everything in the store, not because of the handful of phrasings under review.
        allow = {int(x) for x in a.only.split(",") if x.strip()}
        ix = [i for i, r in enumerate(rows) if r["id"] in allow]
        if len(ix) != len(allow):
            print(f"note: {len(allow) - len(ix)} of the {len(allow)} named ids are not active rows")
        rows, docs, uniq, pays = ([rows[i] for i in ix], [docs[i] for i in ix],
                                  [uniq[i] for i in ix], [pays[i] for i in ix])
    gr, skipped = groups(rows, df, bar)
    exclude = {int(x) for x in a.exclude.split(",") if x.strip()}
    drop, keep_ids, subject_vetoed, content_vetoed = [], [], 0, 0
    for key, idx in sorted(gr, key=lambda kv: (kv[0][0], min(kv[1]))):
        # survivor: NOT meta (a conversation about a fact never outlives the fact - not even when
        # the commentary is declarative and the fact rides in a question-with-answer: measured,
        # "the user asked where KMNE-FM is licensed" beat the row that CONTAINED Bassett) >
        # declarative > most informative text > oldest row
        best = sorted(idx, key=lambda i: (not meta(rows[i]["text"]), declarative(rows[i]["text"]),
                                          len(norm_text(rows[i]["text"])), -rows[i]["created_at"]))[-1]
        keep_ids.append(rows[best]["id"])
        for i in idx:
            if i != best:
                # v3 subject rule: a loser that names a rare subject the winner never mentions is
                # a DIFFERENT fact (Paddock vs Zippel, Edgeley vs Casselton) - retire it and recall
                # loses data, which is exactly what the 94 round-3 restorations proved.
                if uniq[i] - docs[best]:
                    subject_vetoed += 1
                    continue
                # v3 containment rule (same day, round 3 aftermath): shared SUBJECT is not shared
                # CONTENT - six "the project's test suite has 780 tests" rows grouped with the
                # project-LOCATION row (the slug strata-mam-cartrige-mod is itself 4 rare tokens)
                # and nearly retired by a winner that says nothing about tests. A loser drops only
                # when the winner's payload CONTAINS the loser's: the winner must say everything
                # the loser says. Similar-but-different facts all veto here; true paraphrases
                # (equal or subset payloads) still merge.
                if not pays[i] <= pays[best]:
                    content_vetoed += 1
                    continue
                drop.append((rows[i]["id"], rows[best]["id"], rows[i]["text"], rows[best]["text"]))

    kept_drop = [d for d in drop if d[0] not in exclude]
    print(f"memories: {len(rows)}  rare-token bar df<={bar}  payload groups: {len(gr)}  "
          f"mergeable: {len(kept_drop)}"
          f"  negation-skipped: {skipped}  subject-vetoed: {subject_vetoed}"
          f"  content-vetoed: {content_vetoed}  excluded: {len(drop) - len(kept_drop)}")
    # --apply prints EVERY retired pair: a sample is not a review, and round 3 applied 239
    # on 6 shown lines (94 of them retired facts whose data was gone - restored by source tag)
    shown = kept_drop if a.apply else kept_drop[:a.show]
    for loser_id, winner_id, loser, winner in shown:
        print(f"  drop [{loser_id}] {loser[:64]!r}\n    by [{winner_id}] {winner[:64]!r}")
    if not a.apply:
        if len(kept_drop) > a.show:
            print(f"  ... {len(kept_drop) - a.show} more (--apply prints the full list)")
        print("dry run - add --apply to retire the redundant rows (or --hard to delete them)")
        return

    n = 0
    op, okkey = ("forget", "forgotten") if a.hard else ("invalidate", "invalidated")
    for loser_id, winner_id, _, _ in kept_drop:
        body = {"op": op, "id": loser_id}
        if op == "invalidate":
            body["by"] = winner_id
        req = urllib.request.Request(a.url.rstrip("/") + "/memory", method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=30))
        n += 1 if r.get(okkey) else 0
    stats = json.load(urllib.request.urlopen(a.url.rstrip("/") + "/memory", timeout=30))["stats"]
    print(f"applied: {n} {okkey}, store now {stats['memories']} (server stats, not a direct db read)")


if __name__ == "__main__":
    main()
