#!/usr/bin/env python3
"""m4_routegrade - grade the shelf router AS a router, offline on an honest clone (no engine, no boot).

serve/memory.py has voted for shelves on unaddressed requests since 2026-10-17 (route_shelves,
_route, shelf_vote): a bm25-only stage-1 pass (ROUTE_STAGE1 hits, no embedder, no credit), a
rank-weighted vote over the candidates' shelves, the top ROUTE_TOP kept, and a fail-open fallback
when the scoped search comes back EMPTY. The ledger it keeps counts tries, picks and fallbacks -
activity, never accuracy. m4_shelfcall graded what happens when a CALLER names a shelf; nothing
until now graded whether the ROUTER names the right one. That is the gap the C4 capacity probe
inherits, so it gets graded before C4 borrows it.

Two gold legs, because one alone is a trap:

  provenance   every active row carrying provenance.turn_id, queried with the very user text of
               the turn that produced it (truncated [:400] exactly as block_for does). Circular
               by construction - the fact was extracted FROM that text, so the lexical overlap
               is a gift and recall reads high: this is the ceiling, not the traffic.
  hand         ordinary questions in the owner's own voice; gold = the shelf where the answering
               rows ACTUALLY live, verified 2026-10-23 by SQL text probes against the live store,
               not by taxonomy hope. Non-circular phrasing: this is the honest leg.

Laws the grader inherits from the code it grades: a shelf means its SUBTREE (class LIKE prefix%),
so a pick covers a gold row iff the gold's class starts with the pick; a WILD (unclassified) row
votes for NOTHING and matches no prefix, so a WILD gold row can never be addressed - counted, not
hidden; an explicit classes= bypasses the router and is not what is measured here. The block arms
ride production widths (recall_limit, max_block_chars, the embedder arm) and the same fail-open
law the server applies: an empty scoped search falls back to unscoped, so a wrong pick hides gold
only when the scoped block FILLED with the wrong things.

What is scored, per leg:
  abstain    the router stayed unscoped (None) - the safe path, byte-identical to the old store
  hit        some picked shelf covers the gold shelf (subtree law)
  exact      a picked shelf equals the gold shelf
  hidden     picked, nothing covered gold, and the scoped block was NOT empty (it filled wrong)
  rescue     picked, scoped block empty, fallback ran - gold visible again by unscoped bytes
  precision  mean share of picked shelves that cover the gold shelf
Plus the row-level question routing lives or dies on (provenance leg): does the gold ROW ride the
block the router picked versus the block the store would have shown unscoped? loss = shown
unscoped, hidden by the pick; gain = the reverse.

Usage (against the live store, which it never writes):
    .venv/bin/python tools/m4_routegrade.py [--dir DIR] [--model EMBEDDER] [--limit N] [--json F]
"""
import argparse, collections, json, random, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from strata_control.datafolder import data_dir                    # noqa: E402  the one data folder (2026-10-23)
from serve.memory import MemoryService, norm_text                 # noqa: E402
from serve.embedder import Embedder                               # noqa: E402

# Leg 2. Gold shelf verified 2026-10-23 against the live store by SQL (class histogram of rows
# matching the question's keywords) - where the answer LIVES, not where the taxonomy wishes it.
# A query whose topic has no active row (dict_snowball, engine 0.1.48) is not listed: gold must
# point at bytes that exist today.
HAND = [
    ("what temperature do we measure at for pass/fail", "DEC/TEMPE"),
    ("is temperature 0 allowed for a pass/fail measurement here", "DEC/TEMPE"),
    ("why is temperature zero not trusted on this build", "DEC/TEMPE"),
    ("how many repeats before a result counts as pass or fail", "DEC/TEMPE"),
    ("which database is the memory store on now", "DEC/POSTG"),
    ("when does postgres 19 go generally available", "DEC/POSTG"),
    ("do we fork postgres or only take official stable releases", "DEC/POSTG"),
    ("where does the soul text ride now", "MEM/CRG"),
    ("what is the standing text of the cartridge", "MEM/CRG"),
    ("what port does the installer default to", "DEC"),
    ("did we keep tier one of the ladder", "DEC"),
    ("did the date grounding fix ship", "DEC"),
    ("what happened to floor cosine", "DEC"),
    ("did we build our own compression codec for the store", "DEC"),
    ("what does the venue still owe", "MEM/GRADE"),
    ("which runner drives the intelligence bank", "MEM/GRADE"),
    ("is my name allowed in the git history", "USR"),
    ("where do the plan notes live", "USR"),
    ("how is the assistant's name pronounced", "USR/PREF"),
    ("which classification scheme for the memory stack", "USR/PREF"),
    ("what format do quiz answers take", "USR/PREF"),
    ("are replay-derived facts contamination", "USR/PREF"),
    ("should I compact before pasting a long transcript", "USR/PREF"),
    ("how do I want project files summarized", "USR/PREF"),
    ("is the plan file tracked in git", "USR"),
    ("does the server restart on its own or only when I say", "USR/EUGEN"),
    ("do I want a second server instance for probing", "EVAL"),
    ("what do cartridges replace instead of retraining", "USR/LIBRA"),
    ("what are shelves and call numbers for", "USR/LIBRA"),
    ("which tier do I trust long term", "USR/PREF"),
    ("what is the session data for", "USR"),
    ("who is the assistant named after", "USR"),
    ("when were the first knowledge packs ingested", "USR"),
    ("do vetted retrieval facts help a smart model reason", "USR/PREF"),
    ("what grows the model's knowledge as memories are made", "USR"),
    ("does the owner follow a YouTuber", "USR"),
]


def units_provenance(svc, limit):
    """(query, gold rows) pairs from the turns that PRODUCED the facts. A unit is one turn; its
    gold is every active row that turn planted. Checkpoint pastes are excluded - their 'user text'
    is a serialized summary, not a query anybody types."""
    rows = svc.conn.execute(
        "select t.id, t.user_text, m.id, coalesce(nullif(m.class, ''), ''), m.scope"
        " from memories m join turns t on t.id = (m.provenance->>'turn_id')::int"
        " where m.invalid_at is null and m.provenance->>'turn_id' is not null"
        " order by t.id").fetchall()
    by_turn = collections.OrderedDict()
    wild = 0
    for tid, text, rid, klass, scope in rows:
        q = (text or "").strip()
        if len(q) < 20 or q.startswith("<conversation-checkpoint>"):
            continue
        u = by_turn.setdefault(tid, {"q": q[:400], "scope": scope, "gold": [], "wild": 0})
        if klass:
            u["gold"].append((rid, klass))
        else:
            u["wild"] += 1
            wild += 1
    out = [u for u in by_turn.values() if u["gold"]]
    if limit and len(out) > limit:
        random.seed(7)
        out = random.sample(out, limit)
    return out, wild


def grade_one(svc, q, scope, gold_classes, gold_ids):
    """The shipped decision, step by step: the pick; the routed block delivered by the STORE'S OWN
    _router_search - whichever arm the service boots (FILTER with its fail-open fallback, or the
    2026-10-24 DEMOTE arm, where rescue is structurally impossible because nothing was filtered);
    and the unscoped block the store showed before routing existed."""
    pick = svc._route(q, scope)
    rec = {"pick": pick, "abstain": pick is None, "fallback": False,
           "hidden": False, "rescue": False}
    if pick:
        f0 = svc._route_stats["fallbacks"]
        hits = svc._router_search(q, scope, None, True, pick=pick)
        rec["rescue"] = svc._route_stats["fallbacks"] > f0     # the filter arm's fail-open fired
        rec["fallback"] = rec["rescue"]
        _, shown = svc._render_block(hits, svc.max_block_chars, scope) if hits else (None, [])
    else:
        shown = []
    hits_u = svc.search(q, scope=scope, classes=None, semantic=True)
    _, shown_u = svc._render_block(hits_u, svc.max_block_chars, scope) if hits_u else (None, [])
    rec["n_routed"] = len(shown_u) if rec["abstain"] or rec["rescue"] else len(shown)
    rec["n_plain"] = len(shown_u)
    ids_routed = {h["id"] for h in shown}
    ids_plain = {h["id"] for h in shown_u}
    rec["covered"] = bool(pick) and any(g.startswith(p) for g in gold_classes for p in pick)
    rec["exact"] = bool(pick) and any(g == p for g in gold_classes for p in pick)
    if pick and not rec["covered"] and not rec["fallback"]:
        rec["hidden"] = True
    rec["precision"] = (sum(1 for p in pick if any(g.startswith(p) for g in gold_classes)) / len(pick)) \
        if pick else None
    if gold_ids:
        rec["shown_unscoped"] = bool(ids_plain & set(gold_ids))
        rec["shown_routed"] = bool(ids_routed & set(gold_ids)) or (rec["abstain"] and rec["shown_unscoped"]) \
            or (rec["rescue"] and rec["shown_unscoped"])
        rec["loss"] = rec["shown_unscoped"] and not rec["shown_routed"]
        rec["gain"] = rec["shown_routed"] and not rec["shown_unscoped"]
    return rec


def pct(x, n):
    return f"{100.0 * x / n:5.1f}%" if n else "    -"


def report(name, recs, out):
    n = len(recs)
    picked = [r for r in recs if not r["abstain"]]
    abstain = n - len(picked)
    hit = sum(1 for r in picked if r["covered"])
    exact = sum(1 for r in picked if r["exact"])
    hidden = sum(1 for r in picked if r["hidden"])
    rescue = sum(1 for r in picked if r["rescue"])
    prec = [r["precision"] for r in picked]
    out.append(f"\n== {name}: {n} query units ({abstain} abstained, {len(picked)} picked)")
    out.append(f"   of picks: hit(subtree) {pct(hit, len(picked))}   exact {pct(exact, len(picked))}"
               f"   hidden-wrong-block {pct(hidden, len(picked))}")
    out.append(f"   fallbacks (empty scoped block, fail open): {rescue}   "
               f"mean shelf precision: {sum(prec) / len(prec):.3f}" if prec else "   (no picks)")
    nb = [(r["n_routed"], r["n_plain"]) for r in picked]
    if nb:
        mr = sum(x for x, _ in nb) / len(nb)
        mp = sum(y for _, y in nb) / len(nb)
        out.append(f"   block size (rows shown): routed {mr:.1f}   unscoped the same queries would "
                   f"have shown {mp:.1f}   -{100 * (1 - mr / mp):.0f}% of the candidate pool")
    rows = [r for r in recs if "loss" in r]
    if rows:
        su = sum(1 for r in rows if r["shown_unscoped"])
        sr = sum(1 for r in rows if r["shown_routed"])
        lo = sum(1 for r in rows if r["loss"])
        ga = sum(1 for r in rows if r["gain"])
        out.append(f"   gold row in block: unscoped {pct(su, len(rows))}   routed {pct(sr, len(rows))}"
                   f"   LOSS {pct(lo, len(rows))}   GAIN {pct(ga, len(rows))}")
    misses = [r for r in picked if not r["covered"]][:12]
    for r in misses:
        out.append(f"   miss: '{r['q'][:58]}'  pick={','.join(r['pick'])[:40]}  gold={r['gold_show'][:28]}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=str(data_dir(ROOT) / "memory-eval"))
    ap.add_argument("--model", default=str(data_dir(ROOT) / "embeddings/bge-small-en-v1.5"))
    ap.add_argument("--limit", type=int, default=0, help="sample N provenance units (default: all)")
    ap.add_argument("--json", default="", help="write every graded record here")
    ap.add_argument("--block-floor", type=float, default=0.0,
                    help="the production block floor (huihui json: 0.15) - the DEMOTE arm's "
                         "contamination cut IS this floor eating sunk scores, so a run without "
                         "it measures the reorder, not the delivery")
    ap.add_argument("--route-demote", type=float, default=0.0,
                    help="0 = the shipped FILTER arm; 0 < d < 1 = the DEMOTE delivery "
                         "(serve/memory.py _router_search, 2026-10-24) - same picks, nothing hidden")
    a = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="routegrade-"))
    t0 = time.time()
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import _storeclone
        _storeclone.clone_store(Path(a.dir).resolve(), work)      # R5b honest dump/restore, works live
        svc = MemoryService(str(work), recall_limit=96, max_block_chars=48000,
                            embedder=Embedder(a.model, threads=4), route_shelves=True,
                            route_demote=a.route_demote, block_min_score=a.block_floor)
        st = svc.stats()
        out = [f"routegrade: clone of {a.dir} - {st['memories']} active rows, "
               f"{(st.get('embedder') or {}).get('vectors')} vectors, {st['turns']} turns, "
               f"router on ({st['router']['on']}{f', demote x{a.route_demote:g}' if a.route_demote else ''}"
               f"{f', floor {a.block_floor:g}' if a.block_floor else ''})"]

        up, wild = units_provenance(svc, a.limit)
        out.append(f"provenance leg: {len(up)} turn-units ({wild} WILD gold rows counted, never addressable)")
        recs = []
        for u in up:
            g = u["gold"]
            r = grade_one(svc, u["q"], u["scope"], [k for _, k in g], [i for i, _ in g])
            r.update(q=u["q"][:80], gold_show=",".join(sorted({k for _, k in g})), leg="provenance")
            recs.append(r)
        report("provenance (circular by design - the ceiling)", recs, out)

        hand = []
        for q, gold in HAND:
            r = grade_one(svc, q, "default", [gold], [])
            r.update(q=q, gold_show=gold, leg="hand")
            hand.append(r)
        report("hand-labelled (owner's voice - the honest leg)", hand, out)

        hist = collections.Counter(p for r in recs + hand if r["pick"] for p in r["pick"])
        out.append("\npick histogram (what the router reaches for): "
                   + ", ".join(f"{s}:{n}" for s, n in hist.most_common(10)))
        print("\n".join(out))
        if a.json:
            Path(a.json).write_text(json.dumps(recs + hand, indent=1))
            print(f"wrote {a.json}")
        print(f"({time.time() - t0:.0f} s, no engine, no boot; live store untouched)")
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
