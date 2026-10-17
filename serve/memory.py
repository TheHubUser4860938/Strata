"""serve/memory.py — the Memory Core: built-in, harness-independent recall (memory-core fork).

The server keeps a persistent memory of what it has been told through any client, and answers future
clients from it without the harness ever resending memory text: retrieval happens here, the recalled
block is merged into the head of the conversation's leading system turn (the Qwen template allows one
system message only, at index 0 - with no system sent, the block rides as one), and every completed
turn is journaled for curation. Design, measured constraints and the cartridge research track:
docs/MEMORY_CORE.md.

Off unless the engine config carries a "memory" block or the server runs with --memory DIR; with it
absent every code path stays byte-identical to upstream Strata. Storage is NATIVE PostgreSQL
(ADR-002, 2026-10-16 - the user's rebase decision): psycopg 3 against a cluster that starts with
the store (unix socket inside the data dir, so it never touches the network), tsvector + GIN full
text through a custom "strata_fts" config (default parser + english_stem + no stopword list + the
unicode61 apostrophe law via translate()), timestamptz, jsonb provenance, and float32 vectors as
bytea behind the same pinned-numpy exact-scan law. The recall MATH - RRF fusion, the blend
weights, dedup, exposure - is byte-for-byte the engine SQLite shipped with; only the storage is
new. Packaging is a per-OS wheel (embedded-postgres / pixeltable-pgserver) behind one get_server
call; see docs/adr/0002-postgres-native-rebase.md.

Block stickiness is deliberate, and so is its half-life. The block a conversation is SHOWN at the head
is computed once - keyed by scope and the conversation's first user message - and pinned in an LRU
cache, so the rendered prompt keeps its leading bytes stable while Strata's prompt checkpoints reuse
them, even as the store changes underneath. A new conversation or an evicted key picks up the store's
current version. Pinning alone, though, froze recall at the opening question: measured at Gate 1
(2026-10-11, docs/MEMORY_CORE.md "Phase 2") an 8-fact block pinned to the first user message answered
one question per conversation and missed the rest. So per-turn recall rides the TAIL instead: each
request re-searches with the conversation's NEWEST user message and merges what is new (facts the head
block does not already carry) into that message - a handful of bytes at the end of the prompt, where
the bytes change every turn anyway. Head stable, tail fresh: prefix reuse survives and recall follows
the conversation. One switch, "per_turn": false, restores the frozen-head behaviour.

Ideas of numerical memory behind this service are inspired by AKBASCORE MAM / NIRVANA
(https://github.com/ceceli33/titan-cognitive-core-v2, © 2026 Mustafa Akbaş); this implementation is
independent and keeps Strata's MIT license.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SCOPE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}\Z")
USER_SCOPE = "user"                     # the profile shared by every client and project
DEFAULT_PROJECT = "default"             # requests that name no scope land in this project bucket
WORD_RE = re.compile(r"\w{2,}", re.UNICODE)
MAX_QUERY_WORDS = 12                    # a MATCH over more words than these stops being a query and becomes noise
AGE_SCALE_DAYS = 90.0                   # recency half-life-ish: exp(-age_days / this) feeds the ranking blend
RRF_K = 1.0                             # reciprocal-rank fusion constant, a module constant so a probe can
                                        # sweep it (tools/lme_retrieval_ab.py --rrf-k). CHANGED from the
                                        # conventional 60 on 2026-10-14, measured on the venue copy (10,973
                                        # rows, 500 questions, deterministic - one run is one answer):
                                        # hybrid top1 0.504 -> 0.517, top3 0.823 -> 0.849 at k=10, and
                                        # row coverage at the width a venue-shaped block actually shows
                                        # (k=16) 0.932 -> 0.936. So the accuracy effect is ~+0.003, inside
                                        # the repeat band: this is a RANK-ORDER win (it matters to the
                                        # agentic memory_search tool, which returns a short list), not a
                                        # block-content one - at k=192 every constant scores identically.
                                        # Per type it is not free: multi-session +0.033 top1, user +0.016,
                                        # temporal +0.016, preference top3 +0.067, but single-session-
                                        # assistant -0.018, and it does NOT repair the knowledge-update
                                        # -0.139 the reranker costs there. k=1 was chosen over 5/10/20/120
                                        # because it is >= on every metric at every width; Graphiti and
                                        # Zep both ship rank_const=1 for the same fusion, which is what
                                        # prompted the sweep. Sharpening also keeps the property the reach
                                        # probe (tools/m4_reach.py) exists to defend - a lexical first
                                        # place still outscores a both-arms fifth place (1/2 > 1/6) - and
                                        # it widens the adjacent-rank steps the recency/confidence
                                        # tie-breakers must never cross (1/((1+r)(2+r)) > 1.1e-5 through
                                        # rank 300, versus rank ~240 at k=60).
STOP = frozenset(("a an and are as at be by can could did do does for from had has have he her his how i if in "
                  "is it its me my no not of on or our she so than that the their them then there these they this "
                  "to us we what when where which who whom whose why will with would you your").split())
                                      # words that never carry a fact's payload, whatever the corpus says: in a
                                      # five-row test store "the" has df 2 and looks RARE, but it is never a
                                      # subject - excluding them is what stops two different facts about one
                                      # subject (the flibber-gauge pair, measured) from deduping on grammar
CLASS_RE = re.compile(r"[A-Z][A-Z0-9]*(?:/[A-Z0-9]+)*(?:\.[A-Za-z0-9]+)*\Z")
                                      # the dotted tail may repeat, because that is how a real call
                                      # number carries a Cutter and a part: QA76.9.A23 (LCC's
                                      # "Algorithms"), MEM/CRG.3.431 (M3 cartridge banks). The old
                                      # single-optional-tail regex rejected both, including the form
                                      # this file's own docstring advertises.
                                      # a call number, PHASE 4.5: Library-of-Congress style shelf marks -
                                      # STR, MEM/CRG, MEM/CRG.3, MEM/CRG.3.431. The class axis answers
                                      # WHAT a fact is about; scope answers WHO may know it. The two are
                                      # orthogonal, and recall can filter by class prefix the way a
                                      # reader pulls a shelf, not a wing.


def class_name(value, *, quiet: bool = False) -> str:
    """A call number as stored: upper-cased, validated. Spaces are NOT stripped - "engine internals"
    must fail, not silently weld itself into ENGINEINTERNALS. quiet=True (request headers may carry
    anything) drops junk to '' instead of raising."""
    v = str(value or "").strip().upper()
    if not v or CLASS_RE.fullmatch(v):
        return v
    if quiet:
        return ""
    raise ValueError('class must look like a call number: STR, MEM/CRG, MEM/CRG.3.431')


def class_list(classes):
    """Shelf addresses as a caller means them, and IDEMPOTENT (block_for hands the value straight to
    search, so a sentinel that lost its meaning on the second pass would silently widen again - the way
    the first version of this did, caught by test_request_state_class_header).
      None or [] of blanks -> None      no filter, byte-identical to the pre-class store
      a list, >=1 name survives -> the survivors (a half-valid header still filters, as it always has)
      [] (the sentinel) or a list where NONE survives -> [] "an address that names no shelf"
    The last case used to fall through as NO FILTER AT ALL, which is the wrong direction for a filter:
    measured on the live 2,796-row store on 2026-10-15, `classes=["TECH/strata-mam"]` - a name CLASS_RE
    rejects for its hyphen - came back with 96 hits, and all 96 of them were rows from OTHER shelves. A
    caller that asked for the narrowest address in the store was shown the whole store. Fail closed: a bad
    address holds nothing, and the request still works (no exception, no dropped turn), which is what the
    old test actually cared about."""
    if classes is None:
        return None
    items = list(classes)
    raw = [str(x).strip() for x in items if str(x).strip()]
    if not raw:
        return None if items else []      # a list of blanks is no filter; an EMPTY list is the sentinel
    ok = [c for c in (class_name(x, quiet=True) for x in raw) if c]
    return ok if ok else []


def shelf_sql(col: str, prefixes: list[str]) -> tuple[str, list[str]]:
    """A shelf subtree in SQL, spelled out instead of assumed. `col like 'X%'` is correct for LETTER
    segments (POL pulls POL/ARSA, by design) and wrong for DOTTED ones: once decimals are legal call
    numbers, the prefix QA76.9 also answers for QA76.95 and QA76.99 - three different captions in LCC,
    three different sets of facts. A child of X is X.Y or X/Y, never Xyz, so the boundary has to live in
    the predicate. Returns (fragment, params), ready to AND into a WHERE. Measured cost at 2,796 rows:
    none visible (the class column is indexed by nothing either way; the filter is a scan of a set the
    scope filter has already shrunk)."""
    ors, params = [], []
    for pre in prefixes:
        ors.append(f"({col} = %s or {col} like %s or {col} like %s)")
        params += [pre, pre + ".%", pre + "/%"]
    return "(" + " or ".join(ors) + ")", params


def shelf_mask(classes, col) -> "np.ndarray":
    """The same subtree law for the cosine arm's numpy filter - the two arms must answer for the same
    rows or an A/B between them measures the filter, not the ranker. numpy is imported lazily here, as it
    is everywhere in this file, so a store running with the embedder off never pays for it."""
    import numpy as np
    out = np.zeros(len(col), dtype=bool)
    for pre in classes:
        out |= (col == pre) | np.char.startswith(col, pre + ".") | np.char.startswith(col, pre + "/")
    return out


def shelf_key(classes) -> str:
    """The shelf part of a block-pin key. Valid lists hash exactly as they always did; the sentinel gets
    its own marker so a call that addressed a nonexistent shelf can never answer a genuinely unscoped
    question with an empty block."""
    ok = class_list(classes)
    if ok is None:
        return ""
    return ",".join(sorted(set(ok))) if ok else "-"


# THE QUERY->SHELF ROUTER (2026-10-17; law ported verbatim from the filed measurement -
# tools/m4_shelf_nav.vote + the router arm of tools/m4_shelfcall.py, docs/MEMORY_CORE.md
# "Shelf isolation" 2026-10-15). Routing as a RECALL lever is dead (m4_shelf_nav: an oracle shelf
# scored exactly the unfiltered baseline, and the two-stage router measured -0.031). It was reborn
# on the metric the same probe family named: CONTAMINATION. At the shipped 96/24k budget the bm25-vote
# router cut leak 2-5x (MEM 69.1 -> 14.2%, EVAL 41.4 -> 4.9%) with routed reach IDENTICAL to unscoped
# reach in every row. The deferral trigger has fired: the venue store is 10,973 rows, past the 10k
# floor at which RESEARCH.md parked this ("return only at 10k-100k rows").
#   The law, because both halves of the trade were measured: a RIGHT shelf pick gains ~nothing for
# recall and a WRONG one filters the gold out of the candidate pool ENTIRELY. So the router only ever
# speaks where nobody addressed anything (an explicit classes= wins, always), it votes only on shelves
# the CHEAP lexical pass actually landed on (no LLM - the "never add an LLM call without an
# unambiguous signal" rule RESEARCH.md took from Cognee), and it fails OPEN: a pick that finds nothing
# falls back to the unscoped search, which can only ADD what unscoped would have shown, never hide it.
ROUTE_W = [1.0, 0.85, 0.7, 0.6, 0.5, 0.45, 0.4, 0.35, 0.3, 0.25] + [0.2] * 3  # rank weights, top 13
ROUTE_STAGE1 = 12      # cheap-pass hits the vote reads - the probe's number, kept for parity
ROUTE_TOP = 3          # shelves kept - the probe's number, kept for parity


def shelf_vote(hits: list[dict]) -> list[str]:
    """The router's vote: rank-weighted over the shelves the cheap pass landed on, top ROUTE_TOP kept.
    A hit on a WILD (unclassified) row votes for NOTHING - inventing a "no shelf" shelf would let
    unclassified rows out-vote classified ones (m4_shelf_nav's law, and the dev store keeps ~20% wild).
    Ties break by shelf name ASC, exactly as the probe sorted, so the tools and the store pick the same
    shelves from the same votes."""
    tally: dict[str, float] = {}
    for pos, h in enumerate(hits[:len(ROUTE_W)]):
        klass = str(h.get("class") or "")
        if klass:
            tally[klass] = tally.get(klass, 0.0) + ROUTE_W[pos]
    return [s for s, _ in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[:ROUTE_TOP]]


def scope_name(value, fallback: str = DEFAULT_PROJECT) -> str:
    """A scope as stored: kept when it looks like a plain name, else the fallback. Never raises - a request
    header may carry anything."""
    v = (value or "").strip()
    return v if SCOPE_RE.fullmatch(v) else fallback


def norm_text(text: str) -> str:
    """Casefolded, whitespace-collapsed: dedup identity and cache keys."""
    return " ".join("".join([c if c.isalnum() or c.isspace() else " " for c in str(text).casefold()]).split())


def text_of(content) -> str:
    """A message's text from either shape: a plain string, or OpenAI's list of content parts (images are
    skipped - retrieval is over text)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict) and part.get("type") in (None, "text") and isinstance(part.get("text"), str):
                out.append(part["text"])
        return "\n".join(out)
    return str(content)


def memory_dir(value: str, base: str | None = None) -> str:
    """The store directory as one absolute PHYSICAL path - symlinks collapsed, because one
    directory must have one identity (see the realpath law below); relative to the config's
    "cwd" when it has one, created private (mode 0700) when missing. ValueError for anything
    unusable."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('memory "dir" must be a directory path (a string)')
    if any(ord(c) < 32 for c in value):
        raise ValueError('memory "dir" must not contain control characters')
    # REALPATH, not abspath: a store's identity is its PHYSICAL directory. Measured the failure
    # the day it was found (2026-10-16, the cutover itself): the repo's Strata-data is a
    # symlink, the importer resolved it and landed 3,370 rows under one dbname, the server
    # booted with the unresolved path string and opened a FRESH EMPTY database in the SAME
    # cluster - two sha1 names, one directory, one store. realpath() collapses every spelling
    # of one path onto one identity: config, importer, attach() and the tools agree by law.
    path = os.path.realpath(os.path.join(base, value) if base and not os.path.isabs(value) else value)
    os.makedirs(path, mode=0o700, exist_ok=True)
    if not os.path.isdir(path):
        raise ValueError(f"memory dir {path} is not a directory")
    return path


def store_dbname(store_dir: str) -> str:
    """The database name a store dir owns inside its cluster - the same derivation MemoryService
    uses, exposed so tools and importers compute it identically. sha1, not the path text: PG names
    are 63 bytes and dirs are arbitrary."""
    return "m" + hashlib.sha1(memory_dir(store_dir).encode("utf-8")).hexdigest()[:16]


def attach(store_dir: str, **kw):
    """A psycopg connection to a store dir's database, ATTACHING to the cluster that lives inside
    it - the tools' native twin of the old sqlite3.connect(memory.db): one call, same habit. The
    attach law (measured, ADR-002 R0b): attaching starts nothing when a server already runs the
    cluster, and an attacher leaving does not stop it - so the curation sweep and the venue
    harnesses read a live store exactly as they always did. The returned connection is the CALLER's
    to close. kw passes through to psycopg.connect (autocommit=..., row_factory=...)."""
    try:
        import embedded_postgres as _ep
    except ImportError:
        import pixeltable_pgserver as _ep          # type: ignore  # the Windows wheel (ADR-002)
    srv = _ep.get_server(os.path.join(memory_dir(store_dir), "pgdata"))
    return psycopg.connect(srv.get_uri(), dbname=store_dbname(store_dir), **kw)


# The tool in the CHAT TEMPLATE's flat form ({name, description, parameters}) - what MCP's
# template_tools produce and what the renderer's `tool | tojson` line expects. The OpenAI route
# flattens the client's nested tools before this is appended, so flat is the only correct shape.
MEMORY_TOOL = {
    "name": "memory_search",
    "description": ("Search this machine's long-term memory store - facts the user stated, decisions taken, "
                    "and ingested documents, all kept on shelves by subject. Use it whenever the memory block "
                    "already in context is not enough, or you are unsure a fact is really there: ask with the "
                    "words of the question. Narrow with class (a shelf prefix like POL, TECH, USR, or MEM/CRG) "
                    "and/or since/until dates (YYYY-MM-DD) for when-said questions. Call it directly, do not "
                    "narrate first. Answer from the facts it returns; if it finds nothing, say so plainly."),
    "parameters": {
        "type": "object",
        "properties": {
            "q": {"type": "string", "description": "the words to recall (a question or key phrase)"},
            "class": {"type": "string", "description": "optional comma-separated shelf prefixes to filter by"},
            "since": {"type": "string", "description": "optional lower date bound: YYYY-MM-DD or epoch seconds"},
            "until": {"type": "string", "description": "optional upper date bound: YYYY-MM-DD or epoch seconds"},
            "limit": {"type": "integer", "description": "max facts to return, 1-20 (default 8)"},
        },
        "required": ["q"],
}
}


def _tool_done(t0: float, ok: bool, text: str) -> dict:
    return {"ok": ok, "text": text, "chars": len(text), "truncated": False,
            "ms": int((time.perf_counter() - t0) * 1000)}


def tool_result(mem: "MemoryService", arguments, scope: str = "default",
                classes: list[str] | None = None) -> dict:
    """Run one memory_search call from the model (C, agentic recall 2026-10-12). Returns the same
    dict shape the MCP tool loop produces (ok/text/chars/truncated/ms) so run_with_mcp treats both
    executors alike. The search is scoped like the request that enabled the tool: same project plus
    the user scope. The shelf filter differs on purpose: a call that names classes browses to those
    shelves even when the conversation's header filter rides different ones - the header decides
    what every turn carries passively, the tool is the model reaching for another shelf on purpose,
    which is exactly the browsing this feature exists to give it. A call naming no classes inherits
    the conversation's filter, so a filtered conversation never silently widens."""
    t0 = time.perf_counter()
    args = dict(arguments or {})
    q = str(args.get("q") or "").strip()
    if not q:
        return _tool_done(t0, False, '{"error": "q (the words to recall) is required"}')
    try:
        limit = max(1, min(20, int(args.get("limit") or 8)))
        named = [x.strip() for x in str(args.get("class") or "").split(",") if x.strip()]
        # An explicit class argument that names no shelf returns nothing, not the whole store (see
        # class_list); no argument at all still means the server's default classes, byte-identical.
        cls = class_list(named) if named else classes
        since, until = time_bound(args.get("since")), time_bound(args.get("until"))
    except (TypeError, ValueError) as e:
        return _tool_done(t0, False, json.dumps({"error": f"bad argument: {e}"}))
    hits = mem.search(q, scope=scope, limit=limit, classes=cls, since=since, until=until)
    facts = [{"id": h["id"], "class": h.get("class") or "", "text": h["text"],
              "confidence": h.get("confidence"), "hits": h.get("hits")} for h in hits]
    return _tool_done(t0, True, json.dumps({"found": len(facts), "facts": facts}, ensure_ascii=False))


def prepend_head(messages: list[dict], text: str) -> list[dict]:
    """Merge standing text at the very front of the prompt system - BEFORE the volatile recall
    block that request_state merged in, because the restored cartridge prefix is reused only
    while the bytes match from token 0 (measured 2026-10-12: a per-conversation block ahead of
    the cartridge killed reuse at token ~250). Same one-system merge the block uses: the Qwen
    template allows exactly one system message, at index 0."""
    if not text:
        return messages
    head = messages[0] if messages and isinstance(messages[0], dict) else None
    if head is not None and head.get("role") in ("system", "developer"):
        c = head.get("content")
        if isinstance(c, list):
            merged = dict(head, content=[{"type": "text", "text": text}] + list(c))
        elif isinstance(c, str) and c.strip():
            merged = dict(head, content=f"{text}\n\n{c}")
        else:
            merged = dict(head, content=text)
        return [merged] + list(messages[1:])
    return [{"role": "system", "content": text}] + list(messages)


def time_bound(value) -> float | None:
    """A /memory date-index bound (?since= / ?until=, 2026-10-12): epoch seconds, an ISO date
    (read as UTC midnight), or an ISO datetime. None for empty/None - the default window stays
    byte-identical. Junk raises ValueError, which the API answers with a 400: silently reading
    "last tuesday" as "no bound" would widen the window without anyone noticing."""
    if value is None or str(value).strip() == "":
        return None
    s = str(value).strip()
    try:
        return float(s)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s[:19], fmt).replace(
                tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            continue
    raise ValueError(f"bad date {s!r}: use YYYY-MM-DD, an ISO datetime, or epoch seconds")


AUTO_SHELF_CAP = 200      # the most LEARNED sub-drawers one store may auto-mint; past the cap the
                          # taxonomy goes back to human review (m4_shelves propose mode)

FTS_CFG = "strata_fts"    # the store's OWN text-search configuration, created by the schema below.
_TSV_TOK = re.compile(r"'((?:[^']|'')*)':([0-9,]+)")
# one tsvector ENTRY: 'lexeme':1,4,7A. The position list is the term frequency - the only exact
# tf a bare tsvector column offers (tsvector_to_array deduplicates its lexemes; measured,
# 2026-10-16), and _bm25 reads it straight off the ts::text the candidate select already carries.
                          # PG 18 has no porter dictionary at all (measured, ADR-002 R0: pg_ts_dict
                          # lists only snowball *_stem + simple), so the native answer to FTS5's
                          # 'porter unicode61' is: default parser + english_stem + NO stopword list.
                          # The unicode61 apostrophe law rides the generated column instead
                          # (translate(lower(text), '''', ' ')): measured "user's" indexes as user
                          # and a user query matches (True); hyphens index the whole token AND the
                          # parts - a SUPERSET of FTS5's splits, so recall gains matches and never
                          # loses one; snowball's built-in stop list drops don/t/the/s on BOTH sides
                          # of the match, self-consistent. The venue re-record (ADR-002's gate) is
                          # what measures the snowball-vs-porter residue, row by row if it breaks.

_PG_DDL: tuple[str, ...] = (
    # One statement per entry (psycopg has no executescript; the loop is the script). The shapes
    # are the ADR-002 native answers, each replacing a named SQLite-ism from the audit: IDENTITY
    # for AUTOINCREMENT, timestamptz for REAL epoch, jsonb for TEXT-json provenance and the
    # exposure ledger, a STORED generated tsvector + GIN for the FTS5 external-content table and
    # its three triggers (the column IS the index entry - no trigger to keep it honest), ON DELETE
    # CASCADE for the embeddings pair SQLite could only do by convention.
    """create table if not exists memories(
         id bigint generated always as identity primary key,
         scope text not null,
         text text not null,
         source text not null default 'manual',
         provenance jsonb,
         confidence double precision not null default 0.8,
         created_at timestamptz not null,
         updated_at timestamptz not null,
         last_seen_at timestamptz,
         hits integer not null default 0,
         invalid_at timestamptz,
         superseded_by bigint,
         class text not null default '',
         ts tsvector generated always as
             (to_tsvector('strata_fts', translate(lower(text), '''', ' '))) stored)""",
    "create index if not exists mem_ts_gin on memories using gin(ts)",
    "create index if not exists mem_updated on memories(updated_at)",
    "create index if not exists mem_scope_active on memories(scope) where invalid_at is null",
    """create table if not exists shelves(
         name text primary key,
         created_at timestamptz not null,
         source text not null default 'seed',
         anchor text not null default '')""",
    """create table if not exists turns(
         id bigint generated always as identity primary key,
         scope text not null,
         user_text text not null,
         assistant_text text not null,
         model text,
         created_at timestamptz not null,
         processed integer not null default 0,
         injected jsonb)""",
    "create index if not exists turns_pending on turns(id) where processed = 0",
    "create table if not exists meta(key text primary key, value text not null)",
    """create table if not exists embeddings(
         id bigint primary key references memories(id) on delete cascade,
         vec bytea not null)""",
)

class MemoryService:
    """The store, the recall and the journal. Thread-safe: every statement runs under one lock on a
    single psycopg connection (ADR-002: one writer, now with real transactions - the lock keeps the
    single-owner semantics the SQLite era shipped with; MVCC multi-writer is a measured upgrade for
    after the flip, not a day-one change)."""

    def __init__(self, directory: str, *, pg_uri: str | None = None,
                 max_block_chars: int = 6000, recall_limit: int = 8,
                 cache_entries: int = 64, inject: bool = True, per_turn: bool = True,
                 turn_recall_limit: int = 8, turn_block_chars: int = 2000,
                 max_memories: int = 0, dedup_recall: bool = True,
                 encrypt: bool = False, key_file: str = "", passphrase_env: str = "",
                 tool: bool = False, tool_rounds: int = 3, embedder=None, floor_cosine: float = 0.0,
                 auto_shelve: bool = False, block_min_score: float = 0.0,
                 expose_window: int = 0, expose_step: float = 0.0, expose_cap: int = 3,
                 route_shelves: bool = False):
        self.directory = memory_dir(directory)      # absolute, created 0700 - same rules as slot_save_path
        # STORAGE (ADR-002 native rebase, 2026-10-16): PostgreSQL, no SQLite anywhere. Production
        # OWNS its cluster under <dir>/pgdata - a unix socket inside the data dir, so the store
        # never touches the network and the 127.0.0.1-only law is satisfied by construction. Tests
        # and multi-store boxes pass pg_uri and get ONE DATABASE PER STORE DIR inside a shared
        # cluster: createdb measured 3 ms (ADR-002 R0), so per-store isolation costs nothing and
        # the 431-test suite keeps its 431 independent stores without paying 0.3 s of initdb each.
        # Packaging is a per-OS wheel behind one get_server call (embedded-postgres on Linux/macOS,
        # pixeltable-pgserver on Windows); an attaching process that did not start the cluster does
        # not stop it when it leaves - measured, ADR-002 R0b, the law close() depends on.
        self.pgdata = os.path.join(self.directory, "pgdata")
        self._pg_uri = pg_uri
        self._srv = None                            # the cluster handle when this store started one
        self._dbname = store_dbname(self.directory)  # one derivation, shared with attach() - the
        # tools and the store must name the database identically or a sweep reads an empty world.
        # RELEVANCE FLOOR (2026-10-16, the everyday-use complaint "it stuffs the budget full of
        # everything it can find"): measured on the journal's own turns.injected record, 8.2% of
        # injected rows shared a payload with the question, and the same rows rode 8 of 12 turns
        # regardless of topic - the cosine arm's HUB problem, already named in the fusion comment
        # ("a store that size holds dozens of rows at cos 0.5-0.6 on any topical query"). The floor
        # makes the cosine arm vouch only for rows it actually believes in; the budget then stops
        # by relevance instead of by quota. 0.0 = off = every ranking byte-identical to before;
        # the shipped default flips only with the before/after numbers (tools/m4_stuffing.py) and
        # the coverage guard (lme_retrieval_ab --offline) behind it.
        self.floor_cosine = max(0.0, min(1.0, float(floor_cosine)))
        # AUTO-SHELVE (2026-10-16, the user's ask: "the memory system needs to be able to
        # automatically create new categories and cutters as memories are made"). Off by default:
        # every write path byte-identical until a config turns it on. See auto_classify for the
        # rules and why ONLY sub-shelves are auto-mintable.
        self.auto_shelve = bool(auto_shelve)
        # SHELF ROUTER (2026-10-17): the auto-injection path votes the cheap lexical pass into shelves
        # when NOBODY addressed anything (see shelf_vote for the law and why it only fights
        # contamination, never recall). Off = the injection path byte-identical to every earlier
        # release; on = the m4_shelfcall routed arm (leak 2-5x down, reach identical) runs on every
        # unscoped block and tail. The default flips only when the venue re-record says so.
        self.route_shelves = bool(route_shelves)
        self._route_stats = {"tries": 0, "picks": 0, "fallbacks": 0}  # observability, see stats()
        # BLOCK FLOOR (cap-not-quota, m4_blockrule 2026-10-16): _render_block stops taking
        # candidates once the fused RRF score falls below this floor - the budget stays as the
        # hard ceiling, but a full block of weak rows no longer beats an honest short one.
        # 0.0 (the default, and the shipped path until the venue sweep says otherwise): the
        # gate never fires, byte-identical to the quota behaviour this feature replaces.
        # Chosen over a cosine floor because floor_cosine shipped OFF the same day: gating the
        # semantic arm's VOTE cost -7.2 points of gold coverage; this gates what the block SHOWS,
        # both arms alike, and rides the score fusion the retrieval A/B already stands behind.
        self.block_min_score = max(0.0, float(block_min_score))
        # EXPOSURE-DECAY (2026-10-16, the lever the m4_blockrule sweep pointed at): a row the last
        # expose_window journaled turns already injected pays step*min(exposures, expose_cap) off
        # its fused score. The ledger is turns.injected - what the model really read, not what
        # recall proposed. Off until both knobs are set (step 0 or window 0 = byte-identical):
        # the hub-row filler the stuffing complaint named scores 0.17-0.5 and survives every
        # coverage-safe floor (m4_blockrule), so the demotion has to be proportional to how often
        # a row has been shown lately - and capped, so a re-asked fact always outvotes its own
        # history (two fresh top votes beat any cap).
        self.expose_window = max(0, int(expose_window))
        self.expose_step = max(0.0, float(expose_step))
        self.expose_cap = max(1, int(expose_cap))
        self._expo = None          # {(turns stamp): {row id: exposures}} - cached, see _exposure
        self.max_block_chars = max(200, int(max_block_chars))
        # the 50-note ceiling was sized to the 6k/12k char budgets: ~250 chars per note means 50
        # notes IS ~12.5k chars, so a config that raises max_block_chars past that could never
        # fill. Raised to 120 for the injection-budget venue (24k chars / 96 notes), and to 240 for
        # its third arm (48k chars / 192 notes) - the same wall, found before the run this time
        # because the banner check caught the first one as a silent no-op. The guard still stops a
        # runaway config, and the shipped default (48 notes / 12k) is untouched: the clamp only
        # binds above 50, so every earlier release reads byte-identically through it.
        self.recall_limit = max(1, min(240, int(recall_limit)))
        self.per_turn = bool(per_turn)
        self.turn_recall_limit = max(1, min(50, int(turn_recall_limit)))
        self.turn_block_chars = max(200, int(turn_block_chars))
        self.inject = bool(inject)
        # agentic recall (C, 2026-10-12): memory_search offered as a tool the MODEL calls mid-answer,
        # executed by the SERVER (run_with_mcp) - every harness gets browsing with zero client wiring.
        # Off by default: no tool joins the request's list, the prompt bytes stay as they were.
        self.tool = bool(tool)
        # per-request rounds the tool loop runs for THIS store (2026-10-17, the owner's ask after he
        # read the banner's "X-Strata-Memory-Tool: 1 per request" as a count - it is not, 1 is the
        # header's ON value: the count was a bare 3 in run_with_mcp, invisible, until now). 0 =
        # unlimited: the model browses its own store at will while it thinks, and stops when the
        # thinking is done - every round re-renders and spends tokens, and the context window is the
        # only wall left, so a runaway loop is slow, never endless. Default 3 keeps every existing
        # install byte-identical; out of range (negative, > 240) reads as the default.
        _tr = int(tool_rounds)
        self.tool_rounds = _tr if 0 <= _tr <= 240 else 3
        # PHASE 4 semantic layer (opt-in, 2026-10-14): an embedder INSTANCE (duck-typed on
        # embed_query/embed_docs - tests inject stubs) or a config object {"dir": ..., "threads": ...}
        # that builds serve.embedder.Embedder here. Absent = lexical-only recall, byte-identical to
        # every earlier release. A failed load DEGRADES loudly - bm25 keeps answering, stats() carries
        # the error - because a broken model file must never take the memory down.
        self.embedder_error: str | None = None
        if isinstance(embedder, dict) and embedder:
            try:
                from .embedder import Embedder
                embedder = Embedder(embedder.get("dir") or "", threads=int(embedder.get("threads") or 4))
            except Exception as exc:                        # missing file, pin mismatch, bad onnx...
                self.embedder_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                embedder = None
        self.embedder = embedder if embedder is not None else None
        self._vmeta = None                                  # cached (ids, mat, scope, class, ...) or None
        self._vdirty = True
        self._bm25_n: int | None = None                     # cached (n, avgdl) for the lexical arm
        self._bm25_avgdl: float | None = None               # - _bm25_stats() recomputes when n moves
        self._ecount = -1
        self._qc: "OrderedDict[str, object]" = OrderedDict()  # normalized query -> vector, capped at 128
        self._svec: "OrderedDict[str, tuple]" = OrderedDict()  # scope -> (stamp, {row id: vector}), capped at 32
        #   ^ the write guard's candidate set is ONE scope (the same boundary exact-text dedup uses), so
        #   it reads its scope's vectors instead of the whole-store matrix: rebuilding the matrix per
        #   write is what made ingest quadratic - measured 2026-10-14, 7 ms per write at 2,589 rows and
        #   ~180 ms at ~10k. Search still uses the whole-store matrix: a query spans every scope.
        self._dcache: dict | None = None                    # _dedupe's (toks, df, bar, payload memo)
        self._dkey: tuple | None = None                     # active-row stamp the table was built at
        # near-dupe suppression at recall (measured on the live store 2026-10-12: the web venue watched
        # one Qwen3.8 fact take two of its eight slots; the store held FIVE phrasings of it). Off with
        # dedup_recall=False; the search() ranking itself is untouched - this only stops the same fact
        # riding twice under different words.
        self.dedup_recall = bool(dedup_recall)
        # AT REST (ADR-002 retired the seal/reveal file mechanics - they were one-file rules and a
        # Postgres data dir is many files). The native story: the store dir and the cluster inside
        # it are 0700 (memory_dir creates it so; initdb inherits), and the durability/privacy line
        # moves to ENCRYPTED pg_dump BACKUPS under this same key-file - R6 ships the backup tool.
        # encrypt/key_file/passphrase_env stay as accepted config so every server config boots
        # unchanged; the key-file itself is created 0600 because backups will need it. The old
        # threat model stays true in one sentence: a running store is readable by whoever owns the
        # machine; a stopped one is only as private as its data dir, and its backups are ciphertext.
        self.encrypt = bool(encrypt)
        self.key_path = key_file or os.path.join(self.directory, "memory.key")
        self.passphrase_env = passphrase_env or ""
        self._writes = 0
        # budget (M2 job 5, opt-in: 0 = no cap, the path stays byte-identical to every earlier release).
        # Over-cap writes evict the WORST never-recalled rows first (lowest confidence, then oldest) and
        # NEVER touch a fact the recall has actually served (hits>0) - those retire only by human review
        # or the curation loop.  stats() reports "pressure" at 80% of the cap (the MemGPT watermark idea:
        # say so before the wall, so the sweep consolidates instead of the store silently overflowing).
        self.max_memories = max(0, int(max_memories))
        self.lock = threading.RLock()   # re-entrant: the write path now reads its own caches
                                        # (_vec_matrix / _dedupe_tables) from inside remember(), and
                                        # a plain Lock would deadlock the first near-dupe check.
                                        # Single-owner semantics are unchanged - other threads still
                                        # wait for the whole write, commit included.
        self._blocks: OrderedDict[tuple[str, str], tuple[str, list[int]]] = OrderedDict()  # (scope, key) -> pinned (block, shown ids)
        self._cache_entries = max(1, int(cache_entries))
        if self.encrypt:
            self._key_bytes()                                   # create the 0600 key-file when missing
        self._startup()

    def _startup(self) -> None:
        """Connect and own the schema - the three native steps the SQLite one-liner never needed.
        (1) The CLUSTER: production starts or reattaches one under <dir>/pgdata (the unix socket
        lives inside the data dir - the store never touches the network); pg_uri mode creates one
        DATABASE per store dir in a shared cluster, the test-harness law measured at 3 ms per
        store. An attaching process that did not start the cluster does not stop it on the way
        out (measured, ADR-002 R0b) - which is what lets the curation tool read a live store.
        (2) The TEXT-SEARCH CONFIG before the table: the generated tsvector column resolves
        'strata_fts' at CREATE TABLE time, so the config must exist first (PG has no IF NOT EXISTS
        for configurations - the pg_ts_config probe is the idempotency).
        (3) The DDL and the migration law: the runtime add-a-column-on-first-open history that
        pragma table_info carried survives through information_schema; a fresh store passes every
        check because the DDL already carries what the migrations once patched in."""
        if self._pg_uri:
            self._uri = self._pg_uri
        else:
            try:
                import embedded_postgres as _ep
            except ImportError:
                try:                                    # the Windows wheel (ADR-002 per-OS branch)
                    import pixeltable_pgserver as _ep   # type: ignore
                except ImportError:
                    _ep = None
            if _ep is None:
                raise RuntimeError(
                    "the memory store needs a Postgres server: pip install embedded-postgres "
                    "(Linux/macOS) or pixeltable-pgserver (Windows), or pass pg_uri= for an "
                    "existing cluster")
            self._srv = _ep.get_server(self.pgdata)
            self._uri = self._srv.get_uri()
        self.db_path = self.pgdata if self._srv is not None else f"{self._dbname}@cluster"
        # CREATE DATABASE cannot run inside a transaction, so the probe and the maker ride their
        # own autocommit connections; the store connection opens against the db either way.
        with psycopg.connect(self._uri, autocommit=True) as probe:
            have = probe.execute("select 1 from pg_database where datname = %s",
                                 (self._dbname,)).fetchone()
        if not have:
            with psycopg.connect(self._uri, autocommit=True) as maker:
                maker.execute('create database "%s"' % self._dbname)
        self.conn = psycopg.connect(self._uri, dbname=self._dbname)
        with self.lock:
            if not self.conn.execute("select 1 from pg_ts_config where cfgname = %s",
                                     (FTS_CFG,)).fetchone():
                self.conn.execute(f"create text search configuration {FTS_CFG} (parser = default)")
                self.conn.execute(
                    f"alter text search configuration {FTS_CFG} alter mapping for "
                    "word, asciiword, hword, hword_asciipart, hword_part, numword, asciihword "
                    "with english_stem")
                self.conn.commit()
            for stmt in _PG_DDL:
                self.conn.execute(stmt)
            self.conn.commit()
            row = self.conn.execute("select value from meta where key='version'").fetchone()
            if row is None:
                self.conn.execute("insert into meta values('version','1')")
                self.conn.commit()
            # M2 job 7 (the Zep/Graphiti pattern): a correction SUPERSEDES the fact it replaces -
            # invalid_at + superseded_by turn every overwrite into a version chain the review list
            # can walk, instead of a delete that loses the ledger. The runtime-migration law that
            # pragma table_info carried survives natively: information_schema is the discovery,
            # and a FRESH store passes every check because the DDL above already carries what the
            # migrations once patched in (timestamptz where SQLite had REAL, bigint where it had
            # INTEGER, jsonb where it had TEXT - the ADR-002 type answers).
            cols = {r[0] for r in self.conn.execute(
                "select column_name from information_schema.columns where table_name = 'memories'")}
            if "invalid_at" not in cols:
                self.conn.execute("alter table memories add column invalid_at timestamptz")
            if "superseded_by" not in cols:
                self.conn.execute("alter table memories add column superseded_by bigint")
            # PHASE 4.5 first column: the class axis (call number). '' = unclassified, and unclassified
            # is WILD by design - a fact nobody shelved still surfaces in unfiltered recall exactly as
            # before, so every byte of the pre-classification store keeps behaving identically. Only a
            # request that ASKS for a class prefix narrows to shelved rows; the default path never does.
            if "class" not in cols:
                self.conn.execute("alter table memories add column class text not null default ''")
            # The journal's own provenance: WHICH rows the server showed on that turn. request_state has
            # carried the list in ctx since the block existed and the turns table never kept it, which
            # is why "is this curated fact memory feeding on itself?" could not be answered at all -
            # the lexical audit in tools/m4_selffeed.py calls 42.6% of curated facts UNMATCHED because
            # paraphrase, not provenance, is all it can see. One nullable column and no backfill: rows
            # written before it keep NULL and stay attributable by turn_id alone, and the curation
            # loop's behaviour does not change until something asks for the column.
            tcols = {r[0] for r in self.conn.execute(
                "select column_name from information_schema.columns where table_name = 'turns'")}
            if "injected" not in tcols:
                self.conn.execute("alter table turns add column injected jsonb")
            self.conn.commit()

    # ---- store ---------------------------------------------------------------

    def version(self) -> int:
        with self.lock:
            return int(self.conn.execute("select value from meta where key='version'").fetchone()[0])

    def _bump(self) -> None:
        self.conn.execute("update meta set value = cast(cast(value as integer) + 1 as text) where key='version'")

    def _execmany(self, sql: str, seq) -> None:
        """psycopg has no executemany ON THE CONNECTION (the sqlite3 module did) - the cursor
        does; one named twin so the four batch writers read the same way they always did."""
        with self.conn.cursor() as cur:
            cur.executemany(sql, seq)

    @staticmethod
    def _dt(epoch: float):
        """epoch seconds -> aware UTC datetime for timestamptz parameters. The API contract keeps
        EPOCH FLOATS everywhere (list/stats/export/search bounds, day_of, the freshness blend) -
        ADR-002's type upgrade is storage-only, converted at exactly this boundary."""
        return datetime.datetime.fromtimestamp(float(epoch), datetime.timezone.utc)

    @staticmethod
    def _ep(dt) -> float | None:
        """timestamptz (an aware datetime from psycopg) back to the epoch float every caller
        downstream already speaks."""
        return None if dt is None else dt.timestamp()


    def auto_classify(self, text: str) -> str:
        """Write-time sub-shelf minting (the user's ask, 2026-10-16: categories and cutters must
        grow as memories are made). The store's own law - "eyeball before you shelve; a false
        shelf HIDES a fact from every filtered request that stops asking for it" (50-group merge
        audit) - is honored by narrowing WHAT auto may do, not by gating every shelf on a human:

          * auto mints SUB-shelves only (Parent/CUTTER). The subtree law makes a wrong child
            survivable: a search on the parent still returns the row, and an unfiltered search
            always does. A wrong TOP shelf is what hides facts - top shelves stay human-gated
            (the m4_shelves propose path).
          * the cutter is the fact's entity token uppercased and cut to 5 chars, LCC-cutter
            style (Klornish-Reach -> FIC/KLORN), validated through class_name() so the address
            grammar can never be broken by a learned name;
          * a drawer opens on the SECOND mention: the entity must already appear in at least one
            active row, AND at least one of those neighbours must already carry a class - the new
            cutter inherits the parent its entity's family already lives under. An entity nobody
            has ever shelved stays wild, exactly as today;
          * cutter collisions do not hijack: if Parent/CUTTER already exists anchored to a
            different entity, this fact stays wild rather than joining a stranger's drawer;
          * AUTO_SHELF_CAP bounds taxonomy explosion.

        The learned taxonomy is STORE data (the shelves table: name, source, anchor), not code -
        a new category needs no deploy and no reboot, which is the whole point the Postgres port
        inherits (GIN over this table is then trivial). The neighbour lookup rides the store's own
        FTS index (tsvector/GIN since ADR-002, FTS5 before it - same exact-token law) - indexed
        from day one, after a dry run caught LIKE's substring
        ghosts shelving facts into drawers named by "judgment" and "official".
        """
        cands: list[str] = []
        seen: set[str] = set()
        for w in re.findall(r"[A-Za-z][A-Za-z0-9'-]{3,}", text):
            if w[0].isupper() and w.lower() not in STOP and w.lower() not in seen:
                seen.add(w.lower())
                cands.append(w)
        if not cands:
            return ""
        now = time.time()
        with self.lock:
            if not self.conn.execute("select 1 from shelves limit 1").fetchone():
                self.conn.execute("insert into shelves(name, created_at, source) "
                                  "select distinct class, %s, 'seed' from memories where class != ''"
                                  " on conflict do nothing", (self._dt(now),))
                self.conn.commit()          # lazy seed: the table learns the shelves the store already has
            if self.conn.execute("select count(*) from shelves where source='learned'").fetchone()[0] \
                    >= AUTO_SHELF_CAP:
                return ""                   # past the cap: propose, do not mint
            # RAREST SUBJECT WINS (docstring law, made real 2026-10-16 by the backfill's dry-run):
            # text order let a passing mention of the store's busiest entity (Strata: 77 wild rows)
            # outvote the fact's actual subject (cognee, longmemeval, onnx) - the drawer would have
            # been named after the visitor, not the house. Count neighbours per candidate first,
            # then try them least-mentioned first: the specific claim on the house wins.
            # NEIGHBOURS ARE EXACT TOKENS, found through the store's own FTS index (learned the
            # same dry-run day): LIKE %judge% counts "judgment" and %office% counts "offi-cial",
            # and those substring ghosts shelved measurement-protocol facts onto POL/JUDGE and
            # Cognee research onto POL/OFFIC. The phrase match (FTS5 then, to_tsquery now) is
            # exact-token AND indexed - it
            # fixes the false families and the scan-cost note in one move.
            counts: dict[str, list] = {}
            for w in cands:
                counts[w] = self.conn.execute(
                    f"select coalesce(m.class,'') from memories m,"
                    f" to_tsquery('{FTS_CFG}', %s) q"
                    " where m.ts @@ q and m.invalid_at is null",
                    ('"' + w.lower().replace('"', '""') + '"',)).fetchall()
            for w in sorted(cands, key=lambda w: (len(counts[w]), cands.index(w))):
                rows = counts[w]
                if not rows:
                    continue                                            # first mention: no drawer yet
                fulls = [r[0] for r in rows if "/" in r[0]]
                if fulls:
                    drawer: dict[str, int] = {}
                    for f in fulls:
                        drawer[f] = drawer.get(f, 0) + 1
                    top, n = max(drawer.items(), key=lambda kv: kv[1])
                    if n >= 2:
                        # REUSE BEFORE MINT (2026-10-17 audit): the entity's family already has
                        # a DRAWER with >= 2 rows in it - join it instead of naming a new cutter
                        # after a passing token (TECH/CALLN: one MCP-SDK fact minted a second
                        # drawer beside the 708-row TECH/MCP its sentence belonged to). ONE
                        # neighbour is not a family: the entity-named law still opens drawers on
                        # second mention (the FIC/KLORN test is that law, and the collision test
                        # proves a stranger's drawer must not hijack).
                        return top
                parents = [r[0].split("/")[0].split(".")[0] for r in rows if r[0]]
                if not parents:
                    continue                                            # family never shelved: stays wild
                parent = max(set(parents), key=parents.count)
                name = f"{parent}/{w[:5].upper()}"
                try:
                    name = class_name(name)
                except ValueError:
                    continue                                            # grammar refuses the address
                cur = self.conn.execute("insert into shelves values(%s,%s,%s,%s) on conflict do nothing",
                                        (name, self._dt(now), "learned", w.lower()))
                if cur.rowcount == 0:
                    anchor = self.conn.execute("select anchor from shelves where name=%s",
                                               (name,)).fetchone()
                    if not anchor or anchor[0] != w.lower():
                        continue                    # same cutter, different entity: do not hijack
                self.conn.commit()
                self._touched()                     # the version stamp moves with the taxonomy
                return name
        return ""


    def remember(self, text: str, scope: str = USER_SCOPE, source: str = "manual",
                 confidence: float | None = None, provenance: dict | None = None,
                 klass: str | None = None, guard: bool = True) -> dict:
        """Store one durable fact. An active row with the same normalized text and scope is updated instead
        of duplicated: the curation loop may propose the same thing many times. klass is the call number
        (class axis, PHASE 4.5): a re-learned fact never RE-SHELVES a row that already has one - the
        shelf is a decision, not a guess to be overwritten - but fills a shelf that is still empty.
        guard=False stands the SEMANTIC near-dupe verdict aside (the row is still embedded, and exact-text
        dedup still applies) for writes that must land complete: a benchmark haystack is evidence, not a
        set of proposals, and consolidation retires rows the gold map still points at - measured, 317
        oracle rows in lme_ scopes were superseded by the guard during one ingest with the embedder on."""
        text = " ".join(str(text).split())
        if not text:
            raise ValueError("memory text is empty")
        if len(text) > 4000:
            raise ValueError("memory text is over 4000 characters - split it into separate facts")
        scope = scope_name(scope, USER_SCOPE)
        klass = class_name(klass) if klass else ""
        if not klass and self.auto_shelve:
            # the auto-shelver only fills in what the caller did NOT name: a caller that named a
            # class (the sweep's keyword map, a human op=remember, correct()) always wins.
            klass = self.auto_classify(text)
        conf = 0.8 if confidence is None else max(0.0, min(1.0, float(confidence)))
        now = time.time()
        # PHASE 4: embed OUTSIDE the lock (~5 ms CPU per note) so writes never serialize on the
        # model; the blob rides the same commit as the row, so a crash leaves a row without a
        # vector (honest: it stays recallable by bm25) - tools/m4_embed.py closes those gaps.
        vec = self.embedder.embed_docs([text])[0] if self.embedder is not None else None
        with self.lock:
            same = self.conn.execute(
                "select id, text, class from memories where scope=%s and invalid_at is null",
                (scope,)).fetchall()
            hit = next((r for r in same if norm_text(r[1]) == norm_text(text)), None)
            if hit:
                self.conn.execute(
                    "update memories set updated_at=%s, confidence=greatest(confidence,%s), class=%s where id=%s",
                    (self._dt(now), conf, hit[2] or klass, hit[0]))
                if vec is not None:   # text unchanged, but the row may predate the embedder
                    self.conn.execute("insert into embeddings values(%s,%s) on conflict (id) do nothing",
                                      (hit[0], vec.tobytes()))
                self.conn.commit()
                self._touched()
                self._vdirty = True
                self._dedupe_amend()        # the text is unchanged: only the stamp moves
                return {"id": hit[0], "deduped": True}
            # PHASE 4 write-time near-dupe guard (embedder ON only; embedder OFF never gets here,
            # so the shipped write path is byte-identical). The curation loop re-derives the same
            # fact in new words every sweep - one username policy landed as 57 rows in two days, and
            # exact-text dedup cannot see any of them. Cosine FINDS the candidates; the DECISION is
            # the merge tool's v3 law, unchanged (see _semantic_dupe). guard=False: the write must
            # land as written (benchmark ingest) - the vector is still stored, only the verdict stands aside.
            supersede: int | None = None
            if vec is not None and guard:
                verdict = self._semantic_dupe(text, same, vec, scope)
                if verdict and verdict[0] == "suppress":
                    self.conn.execute(
                        "update memories set updated_at=%s, confidence=greatest(confidence,%s), "
                        "class=coalesce(nullif(class,''),%s) where id=%s",
                        (self._dt(now), conf, klass, verdict[1]))
                    self.conn.execute("insert into embeddings values(%s,%s) on conflict (id) do nothing",
                                      (verdict[1], vec.tobytes()))
                    self.conn.commit()
                    self._touched()
                    self._vdirty = True
                    self._dedupe_amend()    # the carrier's text is unchanged: only the stamp moves
                    return {"id": verdict[1], "deduped": "semantic"}
                if verdict and verdict[0] == "supersede":
                    supersede = verdict[1]
            new_id = self.conn.execute(
                "insert into memories(scope,text,source,provenance,confidence,created_at,updated_at,class)"
                " values(%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (scope, text, str(source)[:40],
                 Jsonb(provenance) if provenance else None,
                 conf, self._dt(now), self._dt(now), klass)).fetchone()[0]
            if vec is not None:
                self.conn.execute(
                    "insert into embeddings values(%s,%s) on conflict (id) do update set vec = excluded.vec",
                    (new_id, vec.tobytes()))
                if supersede:      # the richer phrasing wins: the older row retires pointing at it,
                    self.conn.execute(       # the Zep chain stays intact - nothing is deleted (invalidate
                        "update memories set invalid_at=%s, superseded_by=%s, updated_at=%s"
                        " where id=%s and invalid_at is null",   # is the same op)
                        (self._dt(now), new_id, self._dt(now), supersede))
            evicted: list[int] = []
            if self.max_memories:
                # the budget line: over cap, the worst NEVER-RECALLED rows go first (lowest confidence,
                # then oldest) - a fact the recall has served is never deleted without a human. The row
                # just written is exempt: an eviction that eats its own input makes the store a sieve.
                n = self.conn.execute("select count(*) from memories where invalid_at is null").fetchone()[0]
                over = n - self.max_memories
                if over > 0:
                    gone = self.conn.execute(
                        "select id from memories where hits=0 and invalid_at is null and id != %s"
                        " order by confidence asc, created_at asc limit %s", (new_id, over)).fetchall()
                    if gone:
                        # ON DELETE CASCADE on embeddings is the native pair-keeping (the DDL note);
                        # the explicit delete stays as the SQLite-era belt, harmless on PG.
                        self._execmany("delete from memories where id = %s",
                                       [(g[0],) for g in gone])
                        self._execmany("delete from embeddings where id = %s",
                                       [(g[0],) for g in gone])
                        evicted = [g[0] for g in gone]
            self._bump()
            self.conn.commit()
            self._touched()
            self._vdirty = True
            # the token table folds this write in instead of rebuilding (see _dedupe_amend): a
            # superseded row leaves the active set, an eviction deletes one, and the new row joins it.
            self._dedupe_amend(added=[(new_id, set(norm_text(text).split()))],
                               retired=([supersede] if supersede else []) + evicted)
            out = {"id": new_id, "deduped": False}
            if supersede:
                out["superseded"] = supersede
            if evicted:
                out["evicted"] = evicted
            return out

    def _semantic_dupe(self, text: str, same, vec, scope: str) -> tuple | None:
        """The write-time near-dupe DECISION, only reached when an embedder is configured. Cosine
        PROPOSES and nothing more: measured 2026-10-14 on the store that had just grown 57 rows of
        one username policy over two sweeps, a duplicate family's own self-similarity runs 0.59 to
        0.92 (median 0.889), while DIFFERENT facts wearing shared boilerplate sit at 0.95-1.00
        ("KCMI is licensed to Terrytown" beside "KZKX is licensed to Seward"). Any single cosine bar
        therefore either merges distinct facts or misses real duplicates - so the verdict is the
        merge tool's v3 law, the one that survived the round-3 data loss: a rare-token SUBJECT one
        side names and the other never mentions vetoes the merge, and the carrier's rare-token
        PAYLOAD must contain the other side's.

        Three verdicts. 'suppress' when an active row already says everything this text says
        (rare-token containment), OR when the two are one fact with drifted wording - decided by the
        READ path's own near-dupe bar, token Jaccard >= 0.75 (measured on this store 2026-10-12),
        because a row the block would collapse into its neighbour anyway costs nothing to skip, and
        skipping is what stopped one policy becoming 57 rows; 'supersede' when this text says
        everything an active row says plus its own detail (the richer row lands, the older retires
        into the version chain - never deleted); otherwise two different facts, both keeping their
        slot. The subject rule is checked FIRST, above every bar: it is what keeps "KCMI is licensed
        to Terrytown" from folding into "KZKX is licensed to Seward" (Jaccard 7/11 = 0.64, and the
        distinct-facts control both laws are measured against). Candidates are limited to the
        WRITING SCOPE, exactly as the exact-text dedup does; the floor is 0.85 (the family's measured
        25th percentile) and at most the best 8 are
        tested, so a write costs one cached matrix read plus a handful of payload computations - it
        never scans every text in the store. The bar is the merge tool's UNCAPPED relative one
        (stricter than the read path's capped 5, and stricter is the safe direction: a missed merge
        costs one slot, a false one hides a fact)."""
        vecs = self._scope_vecs(scope)     # the candidate set is THIS scope, so the vectors are too -
        if not vecs:                        # a store-wide matrix rebuild per write is what made
            return None                     # ingest quadratic (see _scope_vecs)
        tab = self._dedupe_tables()
        df = tab["df"]
        bar = max(3, len(tab["toks"]) // 50)
        cand = []
        for rid, rtext, _rclass in same:
            v = vecs.get(rid)
            if v is None:
                continue                                # a row predating the embedder: no vector to compare
            c = float(v @ vec)
            if c >= 0.85:
                cand.append((c, rid, rtext))
        if not cand:
            return None
        cand.sort(reverse=True)
        t_new = {t for t in norm_text(text).split() if t not in STOP}
        p_new = frozenset(t for t in t_new if df.get(t, 0) <= bar)
        u_new = {t for t in t_new if df.get(t, 0) <= 3}   # the tool's subject rule: df<=3 is a name
        # STOP-excluded on both sides, which the merge tool does NOT do and this guard does: the
        # first live run vetoed a true duplicate over the word "with" (df 1 in a 209-row test store),
        # and a connective is not a subject. Content words only - the veto exists to protect NAMES.
        for _c, rid, rtext in cand[:8]:
            t_ex = {t for t in norm_text(rtext).split() if t not in STOP}
            p_ex = frozenset(t for t in t_ex if df.get(t, 0) <= bar)
            u_ex = {t for t in t_ex if df.get(t, 0) <= 3}
            # 1. the round-3 rule, first: a rare SUBJECT the carrier never mentions is a different
            #    fact, whatever the distance says. The new row may still SUPERSEDE the old one (it
            #    carries everything the old one says, plus its own subject) - that is the survivor
            #    law - but it never folds into it.
            if u_new - t_ex:
                if not (u_ex - t_new) and p_ex <= p_new:
                    return ("supersede", rid)
                continue
            # 2. the carrier already says everything this text says (rare-token containment): the
            #    insert would be a second copy of one fact, so the row is refreshed instead.
            if p_new <= p_ex:
                return ("suppress", rid)
            # 3. same fact, drifted wording: the READ path's near-dupe bar (token Jaccard >= 0.75,
            #    measured on this store 2026-10-12) decides here too - a row the block would
            #    collapse into its neighbour anyway costs nothing to skip, and skipping is what
            #    stopped one policy becoming 57 rows. The bar runs on CONTENT tokens (stop words
            #    out), and the distinct-facts control clears it either way: "KCMI is licensed to
            #    Terrytown" beside "KZKX is licensed to Seward" scores 4/8 = 0.50 here, 0.64 with
            #    the connectives left in - both far under 0.75, exactly as at recall.
            union = t_new | t_ex
            if union and len(t_new & t_ex) >= 0.75 * len(union):
                return ("suppress", rid)
            # 4. this text says everything the carrier says (and adds its own detail): the richer
            #    row wins, the older one retires into the version chain.
            if p_ex <= p_new:
                return ("supersede", rid)
        return None

    def forget(self, memory_id: int) -> bool:
        with self.lock:
            gone = self.conn.execute("delete from memories where id = %s", (int(memory_id),))
            self.conn.execute("delete from embeddings where id = %s", (int(memory_id),))
            self._bump()
            self.conn.commit()
            self._touched()
            self._vdirty = True
            return gone.rowcount > 0

    def invalidate(self, memory_id: int, by_id: int | None = None) -> bool:
        """Take a fact out of recall WITHOUT deleting it: invalid_at marks when the store stopped
        trusting it, superseded_by points at the fact that replaced it (the version chain). Search,
        recall and the budget see only active rows; the review list and any as-of query can still
        walk the history - the honest-ledger rule the merge deletes quietly broke."""
        with self.lock:
            r = self.conn.execute(
                "update memories set invalid_at=%s, superseded_by=%s, updated_at=%s"
                " where id=%s and invalid_at is null",
                (self._dt(time.time()), int(by_id) if by_id else None,
                 self._dt(time.time()), int(memory_id)))
            self._bump()
            self.conn.commit()
            self._touched()
            self._vdirty = True       # same law as classify: the matrix holds ONLY active rows, so
            # an invalidate that does not dirty it leaves the cosine arm offering a superseded fact
            # (its fetch by id does not re-check invalid_at). correct() inherits this fix.
            return r.rowcount > 0

    def correct(self, text: str, old_id: int, scope: str = USER_SCOPE, source: str = "manual",
                confidence: float | None = None, provenance: dict | None = None,
                klass: str | None = None) -> dict:
        """One step, Zep-style: the new fact lands, the old one is invalidated pointing at it.
        An unknown old_id still stores the new fact (a correction of a forgotten fact is a fill).
        A correction never re-shelves its fact (PHASE 4.5): with no class given, the replacement
        inherits the shelf of the row it supersedes - the correction is about the same subject, so
        it belongs on the same shelf, and a fix that drifted classes would hide itself from exactly
        the conversations that need it."""
        if klass is None:
            with self.lock:
                old = self.conn.execute("select class from memories where id = %s",
                                        (int(old_id),)).fetchone()
                self.conn.commit()
            klass = old[0] if old and old[0] else None
        new = self.remember(text, scope=scope, source=source, confidence=confidence,
                            provenance=provenance, klass=klass)
        if new.get("id") == int(old_id):
            # the write-path dedup suppressed this correction INTO the very row it replaces (>=75%
            # token overlap): the new text added nothing the carrier lacks, so the old row IS the
            # survivor - invalidating it by itself would bury a live fact under a self-pointer
            # (measured 2026-10-17: "user's name is Eugene" vs the richer username row, scar 461).
            new["invalidated"] = None
            return new
        self.invalidate(old_id, by_id=new["id"])
        new["invalidated"] = int(old_id)
        return new

    def classify(self, memory_id: int, klass: str) -> bool:
        """Shelve one fact (op=classify, the backfill and the hand-review path). Works on superseded
        rows too - history keeps its shelf marks. The row's own scope stays untouched: class says
        what, scope says who."""
        klass = class_name(klass)
        with self.lock:
            r = self.conn.execute("update memories set class=%s, updated_at=updated_at where id=%s",
                                  (klass, int(memory_id)))
            self._bump()
            self.conn.commit()
            self._touched()
            self._vdirty = True       # the matrix cache carries a CLASS column for the cosine arm's
            # shelf_mask: a classify that does not dirty it leaves the hybrid arm answering for the
            # OLD shelf until some unrelated write rebuilds - the backfill's 122 op=classify calls
            # (2026-10-16) were invisible to the cosine arm while the lexical arm was always right.
            return r.rowcount > 0

    def list_memories(self, scope: str | None = None, limit: int = 100,
                      include_invalid: bool = False, klass: str | None = None) -> list[dict]:
        """The store newest-first. Invalidated (superseded) rows are hidden by default - the caller
        wants what the recall can serve; include_invalid brings back the version history with its
        invalid_at / superseded_by chain for the review list and as-of inspection. klass filters to
        one shelf subtree (prefix match: MEM pulls MEM/CRG with it)."""
        conds, params = [], []
        if scope:
            conds.append("scope = %s")
            params.append(scope)
        if klass:
            named = [c for c in (class_name(x, quiet=True) for x in klass.split(",")) if c]
            if named:
                frag, args = shelf_sql("class", named)
                conds.append(frag)
                params += args
        if not include_invalid:
            conds.append("invalid_at is null")
        params.append(max(1, min(500, int(limit))))
        cols = ",invalid_at,superseded_by" if include_invalid else ""
        where = (" where " + " and ".join(conds)) if conds else ""
        with self.lock:
            rows = self.conn.execute(
                "select id,scope,text,source,confidence,created_at,updated_at,hits,class" + cols
                + " from memories" + where + " order by updated_at desc limit %s", params).fetchall()
            self.conn.commit()
        return [dict(id=r[0], scope=r[1], text=r[2], source=r[3], confidence=r[4],
                     created_at=round(self._ep(r[5]), 1), updated_at=round(self._ep(r[6]), 1),
                     hits=r[7], **{"class": r[8]},
                     **({"invalid_at": round(self._ep(r[9]), 1) if r[9] else None,
                        "superseded_by": r[10]}
                        if include_invalid else {})) for r in rows]

    # ---- recall --------------------------------------------------------------

    @staticmethod
    def _is_declarative(text: str) -> bool:
        """A fact states; a question asks. When the two collide as near-dupes the fact wins - the
        merge tool's survivor rule, applied at injection time (an injected question answers nothing)."""
        t = str(text).strip()
        if t.endswith("?"):
            return False
        first = t.lower().split(maxsplit=1)[0] if t else ""
        return first not in ("who", "what", "when", "where", "why", "which", "how", "whose", "whom",
                             "is", "are", "was", "were", "do", "does", "did", "can", "could", "in",
                             "on", "at", "from", "name", "identify", "list", "state", "tell", "given")

    def _dedupe_tables(self) -> dict:
        """The whole-store token table, its document frequencies and the rare-token bar that
        _dedupe reads. They depend on nothing but the ACTIVE row set, so they are cached and
        rebuilt only when that set moves. The key is (count, max id, max updated_at) of the
        active rows, which catches every write this server makes: an insert or an eviction moves
        the count, an edit bumps updated_at, an invalidation moves the count. Measured at build
        (2026-10-14, the 2,521-row dev store, RTX 4090 box, CPU, limit 96): rebuilding the table
        per recall cost most of the 126 ms a lexical recall took; cached, the same recall measures
        52 ms warm (75 ms right after a write, when the table legitimately rebuilds) and the output
        is unchanged row for row - a 10-query x 2-mode snapshot of 1,920 ordered rows diffed clean.
        An outside editor that rewrote a row's text WITHOUT bumping updated_at would keep the
        older table a while - the effect is the rare-token bar for one pair, not correctness, and
        every writer in this project (the API, the sweep, tools/m2_merge) bumps the stamp.

        The bar cap is the 2026-10-12 airport lesson: at 2,384 rows the uncapped relative bar hit
        47, which made a 6-row sub-corpus's OWN connectives (faa/location/identifier/municipal,
        df 6) look rare, and recall collapsed Westhope/Pruetz/Arthur into one slot. The read path
        caps at 5 - a missed merge costs one slot, a false collapse HIDES a fact with no human to
        spot it; the merge tool (tools/m2_merge) keeps the uncapped relative bar because there a
        human vetoes every group before anything retires."""
        with self.lock:
            key = tuple(self.conn.execute(
                "select count(*), coalesce(max(id),0), coalesce(extract(epoch from max(updated_at)), 0.0)"
                " from memories where invalid_at is null").fetchone())
            if self._dcache is not None and self._dkey == key:
                return self._dcache
            rows = self.conn.execute(
                "select id, text from memories where invalid_at is null").fetchall()
            self.conn.commit()
        toks = {r[0]: set(norm_text(r[1]).split()) for r in rows}
        df: dict[str, int] = {}
        for s in toks.values():
            for t in s:
                df[t] = df.get(t, 0) + 1
        tab = {"toks": toks, "df": df, "bar": max(3, min(len(toks) // 50, 5)), "pay": {}}
        self._dcache, self._dkey = tab, key
        return tab

    def _dedupe_stamp(self) -> tuple:
        with self.lock:
            return tuple(self.conn.execute(
                "select count(*), coalesce(max(id),0), coalesce(extract(epoch from max(updated_at)), 0.0)"
                " from memories where invalid_at is null").fetchone())

    def _dedupe_amend(self, added=(), retired=()) -> None:
        """Fold the write that just happened into the cached token table instead of rebuilding it.

        df depends on nothing but the ACTIVE row set, and every write path in remember() knows
        exactly how it changed that set: which rows appeared, which retired. Rows whose TEXT changed
        do not exist - an in-place update edits updated_at/confidence/class and leaves the text, so
        the token sets and df are untouched and only the stamp needs moving. Without this, a burst of
        writes rebuilt the table once per write: profiled 2026-10-14 on the live store, 119,023
        norm_text calls for 40 writes, 86% of all write time.

        The stamp is then re-read from the db, so the cache is stamped with the true post-write
        state and the NEXT caller still rebuilds on any change this server did not make itself -
        an outside editor, a tool run, a restore. The amendment is the fast path, the stamp check
        stays the safety net. Payloads are dropped on any amendment, which is exactly what a rebuild
        did before: a new row can move a token across the rare-token bar and change what counts as
        another row's payload.
        """
        tab = self._dcache
        self._dkey = self._dedupe_stamp()
        if tab is None:
            return                                    # nothing cached: the next caller builds it
        for rid in retired:
            s = tab["toks"].pop(int(rid), None)
            tab["pay"].pop(int(rid), None)
            for t in s or ():
                n = tab["df"].get(t, 0) - 1
                if n > 0:
                    tab["df"][t] = n
                else:
                    tab["df"].pop(t, None)
        for rid, toks in added:
            rid = int(rid)
            old = tab["toks"].get(rid)
            if old == toks:
                continue
            for t in (old or set()) - toks:
                n = tab["df"].get(t, 0) - 1
                if n > 0:
                    tab["df"][t] = n
                else:
                    tab["df"].pop(t, None)
            for t in toks - (old or set()):
                tab["df"][t] = tab["df"].get(t, 0) + 1
            tab["toks"][rid] = toks
            tab["pay"].pop(rid, None)
        if added or retired:
            tab["pay"].clear()                        # bar and rarity just moved; see the docstring

    def _dedupe(self, hits: list[dict]) -> list[dict]:
        """Near-dupe suppression for the ranked candidates (measured 2026-10-12: five rows phrased the
        same Qwen3.8 fact; the web venue watched two of its eight slots go to it in one block). Three
        rules, any one fires: token Jaccard >= 0.75 catches light rewrites; payload EQUALITY is the
        merge tool's content address (same rare tail = same fact); payload INTERSECTION >= 4 catches
        paraphrase clusters (the five Qwen rows share six rare tokens). A 2-3 token subject match is
        NOT enough here - injection dedups with no human in the loop, and "the flibber gauge reads
        torque" beside "the flibber gauge committee meets in Itasca" are two facts worth both slots
        (measured, this test suite). Payloads exclude STOP words, and the rare-token bar is RELATIVE
        but CAPPED, max(3, min(active_rows // 50, 5)): when the same fact exists five times its
        payload tokens stop being rare - the measured blind spot that made the absolute df<=3 merge
        bar miss this exact cluster - but a bar that keeps growing with the store eventually calls a
        small sub-corpus's own connectives rare and collapses its DIFFERENT facts into one slot
        (the 2026-10-12 airport cascade at 2,384 rows). The read path caps; the merge tool does not.
        A question and its answer collide under these rules; the declarative always wins the slot,
        whoever ranked higher."""
        tab = self._dedupe_tables()
        toks, pays = tab["toks"], tab["pay"]

        def payload(row_id: int) -> frozenset:
            """The rare-token payload of a ROW, memoized for the life of the table. The first
            version recomputed it for every PAIR inside the loop below (profiled 2026-10-14:
            2.5M genexpr calls and 0.9 s of tottime per five recalls) - the answer depends only
            on the row's tokens and the table's df, so once per row is the same answer."""
            p = pays.get(row_id)
            if p is None:
                ts = toks.get(row_id)
                p = frozenset(t for t in ts if t not in STOP and tab["df"].get(t, 0) <= tab["bar"]) \
                    if ts else frozenset()
                pays[row_id] = p
            return p

        kept: list[dict] = []
        for h in hits:
            ts = toks.get(h["id"])
            if ts is None:                                  # row vanished mid-search: keep it, harmless
                kept.append(h)
                continue
            pay = payload(h["id"])
            clash = None
            for k in kept:
                kts = toks.get(k["id"], set())
                union = ts | kts
                kpay = payload(k["id"])
                if (union and len(ts & kts) >= 0.75 * len(union)) or (pay and pay == kpay) or (
                        pay and kpay and len(pay & kpay) >= 4):
                    clash = k
                    break
            if clash is None:
                kept.append(h)
            elif self._is_declarative(h["text"]) and not self._is_declarative(clash["text"]):
                kept[kept.index(clash)] = h                 # the fact bumps its own question from the slot
        return kept

    def cartridge_text(self, prefixes: list[str], max_chars: int = 4000) -> tuple[str, int, int]:
        """The standing text of the tier-2 cartridge (M3b, 2026-10-12): the rows of these shelf
        subtrees, confidence descending then id ascending - never by hits, because hits drift with
        every recall and the cartridge file is only valid for its exact bytes (the engine reuses the
        restored prefix while the prompt matches it, token for token, measured by the spike the same
        night: reuse dies at the first differing token). Returns (text, n_facts, n_left_out); the
        left-out rows still ride the tier-3 block, so a small budget costs standing status, never
        knowledge. Unclassified rows match no prefix: the cartridge holds shelved facts on purpose -
        the call number IS the graduation criterion."""
        ok = [c for c in (class_name(p, quiet=True) for p in prefixes) if c]
        if not ok:
            return "", 0, 0
        frag, args = shelf_sql("class", ok)
        with self.lock:
            rows = self.conn.execute(
                "select id, class, text, confidence from memories where invalid_at is null and "
                + frag + " order by confidence desc, id asc", args).fetchall()
            self.conn.commit()
        header = "STANDING MEMORY - always known; never restate it, never search for it:"
        lines, used, left = [], len(header) + 1, 0
        for i, (_rid, cls, txt, _conf) in enumerate(rows, 1):
            ln = f"{i}. [{cls}] {txt}"
            if used + len(ln) + 1 > max_chars:
                left += 1
                continue
            lines.append(ln)
            used += len(ln) + 1
        if not lines:
            return "", 0, left
        return header + "\n" + "\n".join(lines), len(lines), left

    def _bm25_stats(self) -> tuple[float, float]:
        """(n, avgdl) for the lexical arm - FTS5 computed document stats over the whole table for
        every query; a bare Postgres tsvector column carries no hidden equivalent, so the store
        keeps its own: the active-row count is one cheap probe, and avgdl comes from ts_stat's
        nentry tally, CACHED and recomputed only when the count moved (the curation timer inserts
        every ~30 min; keying the cache on n keeps it honest without a restart). Measured on the
        venue store (11k rows): the full ts_stat pass costs a few tens of ms, once per change."""
        with self.lock:
            n = self.conn.execute(
                "select count(*) from memories where invalid_at is null").fetchone()[0]
            if self._bm25_n != n or self._bm25_avgdl is None:
                tot = self.conn.execute(
                    "select coalesce(sum(st.nentry), 0) from ts_stat("
                    "'select ts from memories where invalid_at is null', 'ts') st").fetchone()[0]
                self.conn.commit()
                self._bm25_avgdl = max(1.0, float(tot) / max(1, n))
                self._bm25_n = n
            return float(n), self._bm25_avgdl

    def _bm25(self, rows: list, qtoks: list, dfm: dict, n: float, avgdl: float,
              limit: int) -> list:
        """REAL bm25 over the @@ candidates - the venue re-record of 2026-10-16 is why ts_rank_cd
        is demoted to a candidate FILTER here: coverage survived the rebase (0.817 -> 0.798, inside
        the floor) but rendered top1 fell 0.394 -> 0.186, and a same-data Python bm25 recovered 20
        of 25 sampled flips. The cause is structural: ts_rank_cd has no inverse document frequency,
        so common words vote as loud as rare ones and the RRF arm inherits the blindness. The law
        here is FTS5's own: Lucene idf = ln(1 + (n-df+0.5)/(df+0.5)), saturation k1=1.2, length
        norm b=0.75 - tf read EXACTLY from the tsvector position lists (tsvector_to_array
        deduplicates, so it cannot serve as tf; measured). df and (n, avgdl) are store-wide, as
        FTS5's table stats were; ties break by id ASC, the same visible-ORDER BY law as before."""
        if not rows or not qtoks:
            return []
        qset = set(qtoks)
        idfc = {w: math.log(1.0 + (n - dfm.get(w, 0) + 0.5) / (dfm.get(w, 0) + 0.5))
                for w in qset}
        k1, b = 1.2, 0.75
        scored = []
        for r in rows:
            tf: dict = {}
            dl = 0
            for mt in _TSV_TOK.finditer(r[7]):
                c = mt.group(2).count(",") + 1        # positions in the list = term frequency
                dl += c
                w = mt.group(1).replace("''", "'")
                if w in qset:
                    tf[w] = tf.get(w, 0) + c
            s = 0.0
            for w, f in tf.items():
                s += idfc[w] * f * (k1 + 1.0) / (f + k1 * (1.0 - b + b * dl / avgdl))
            if s > 0.0:
                scored.append((s, r))
        scored.sort(key=lambda x: (-x[0], x[1][0]))   # score desc, id asc - the visible tiebreak
        return [r for _s, r in scored][:limit * 3]

    def search(self, query: str, scope: str = DEFAULT_PROJECT, limit: int | None = None,
               classes: list[str] | None = None, since: float | None = None,
               until: float | None = None, semantic: bool = True,
               credit: bool = True) -> list[dict]:
        """tsvector/GIN candidates over the user scope plus this request's project scope - the native
        twin of the SQLite-era bm25 arm (ADR-002): to_tsquery ORs with |, the GIN probe supplies the
        candidate set, _bm25() ranks it with REAL Lucene bm25 (ts_rank_cd was tried and rejected the
        day it was measured - no IDF, and the venue top1 fell 0.394 -> 0.186), ties break by id ASC
        in visible code (SQLite's ties fell to rowid order; PG's are undefined), and the arm feeds
        the blend RANK POSITIONS, never rank values. Then
        re-ordered by a blend: 0.75 rank + 0.25 recency + 0.10 confidence, all in [0,1]-ish.
        Returns at most `limit`.
        classes (PHASE 4.5) filters to shelf subtrees by prefix - a conversation about the engine asks
        for ["STR","USR"] and the eval corpus trivia simply is not in the candidate set. Unclassified
        facts ('') match NO prefix: they ride every UNFILTERED request exactly as before, and a request
        that names classes deliberately narrows to what has been shelved.
        since/until (2026-10-12, the Primnox date-index lesson): epoch-seconds bounds on created_at.
        "When did we decide X" and "what did we know last month" are episodic questions, and every row
        already carries its stamp - filtering on it is the date index. Both None (every request today)
        keeps the ranking byte-identical to before. With an embedder configured (PHASE 4) the bm25 set
        is UNIONed with a cosine pass over every embedded active row - see _semantic. `semantic=False`
        (the per-request embedder mute, X-Strata-Memory-Embedder: off) skips that pass and ranks exactly
        as the lexical store does - the A/B within one boot, on one store and one vector set.
        `credit=False` (the router's cheap stage-1 pass, 2026-10-17) skips the _seen bookkeeping: those
        rows were PROPOSED to a vote, never SHOWN to a model, and hits/last_seen_at are the budget
        eviction's "never touch a served fact" guard and what the memory_search tool reports - a router
        that credited its candidates would quietly make every popular shelf's rows eviction-proof
        without ever injecting one. Default True: every existing call site byte-identical."""
        limit = self.recall_limit if limit is None else max(1, min(240, int(limit)))
        # PHASE 4: with an embedder configured, a query bm25 CANNOT serve is exactly the case the
        # cosine pass exists for (spikes/lme/TEMPORAL_DIAG.md: multi-evidence rows that share no rare
        # word with the question). Embedder OFF keeps both early returns untouched - byte-identical.
        classes = class_list(classes)          # a name that is not a call number addresses NOTHING
        if classes is not None and not classes:
            return []                          # ...and not, as it used to, everything
        words = WORD_RE.findall(query.lower())[:MAX_QUERY_WORDS]
        # tsquery OR is | ; WORD_RE words are \w{2,} so no tsquery operator character can leak in
        # unescaped - the closed alphabet replaces the old per-word FTS5 quoting.
        match = " | ".join(words) if words else None
        scopes = [USER_SCOPE]
        s = scope_name(scope)
        if s != USER_SCOPE:
            scopes.append(s)
        shelf = ""
        prefixes = []
        if classes:
            ok = [class_name(c, quiet=True) for c in classes]
            ok = [c for c in ok if c]
            if ok:
                frag, prefixes = shelf_sql("m.class", ok)
                shelf = " and " + frag
        bounds = ""
        bparams: list = []
        for cmp_op, v in ((">=", since), ("<=", until)):
            if v is not None:
                bounds += f" and m.created_at {cmp_op} %s"
                bparams.append(self._dt(float(v)))
        rows: list = []
        if match:
            # bm25 must SEE every candidate to RANK them - the old arm's limit*3 cut the candidate
            # set BY RANK, which only made sense while the rank was itself the law. The GIN probe
            # still supplies the set (measured, ADR-002 R0: 1.6 ms at 2,707 rows); order by id
            # keeps the cap deterministic, and the cap is worst-case honesty: at this store's
            # scale it never binds; a query matching >4000 rows is a different design conversation.
            with self.lock:
                cand = self.conn.execute(
                    f"select m.id, m.scope, m.text, m.confidence, m.updated_at, m.class, m.hits,"
                    f" m.ts::text"
                    f" from memories m, to_tsquery('{FTS_CFG}', %s) q"
                    " where m.ts @@ q and m.invalid_at is null and m.scope in ("
                    + ",".join(["%s"] * len(scopes)) + ")" + shelf + bounds
                    + " order by m.id limit 4000",
                    [match] + scopes + prefixes + bparams).fetchall()
                # the query's STEMMED tokens in one round trip: to_tsvector runs the SAME parser and
                # english_stem as the to_tsquery that matched, so the token sets agree by
                # construction - and df then probes exactly the lexemes the candidates matched.
                qtoks = self.conn.execute(
                    f"select tsvector_to_array(to_tsvector('{FTS_CFG}', %s))",
                    (" ".join(words),)).fetchone()[0] or []
                # df per token, one round trip of correlated GIN probes. The exact-lexeme primitive
                # is @@ at the 'simple' config: no re-stemming (snowball is NOT provably idempotent
                # on its own stems, and df must count the SAME lexemes the candidates matched),
                # index-backed, and WORD_RE+stemmer output can carry no tsquery operator character.
                # (The tsvector ? lexeme operator family I first reached for turned out to be
                # jsonb's, not core PG's - checked against pg_operator on 18.6, measured.)
                # Store-wide stats, as FTS5's table stats were. Tried the single wide-pass shape
                # (one @@ over all tokens, unnest, group): measured SLOWER at this store's scale
                # (26.5 vs 20.7 ms full search, 2026-10-16) - targeted probes beat one union scan
                # while the token count is small, and MAX_QUERY_WORDS bounds the worst case.
                dfm = dict(self.conn.execute(
                    "select t.tok, (select count(*) from memories mm"
                    " where mm.invalid_at is null and mm.ts @@ to_tsquery('simple', t.tok))"
                    " from unnest(%s::text[]) t(tok)", (qtoks,)).fetchall()) if qtoks else {}
                self.conn.commit()
            rows = self._bm25(cand, qtoks, dfm, *self._bm25_stats(), limit)
        if not rows and self.embedder is None:
            return []
        k = len(rows)
        now = time.time()
        out = []
        for pos, r in enumerate(rows):
            fresh = math.exp(-max(0.0, now - self._ep(r[4])) / 86400.0 / AGE_SCALE_DAYS)
            score = 0.75 * ((k - pos) / k) + 0.25 * fresh + 0.10 * r[3]
            out.append(dict(id=r[0], scope=r[1], text=r[2], score=round(score, 4),
                            lex_rank=pos + 1,          # position in the bm25 list, BEFORE the blend
                            confidence=r[3], hits=r[6],  # the memory_search tool shows both; they were
                            #   promised and came back null - confidence fed the blend then got dropped,
                            #   hits was never selected (everyday-use bug report, 2026-10-16)
                            **{"class": r[5]}))        # re-sorting loses it and the blend is not the ranking
        out.sort(key=lambda x: -x["score"])
        if semantic and self.embedder is not None:
            out = self._semantic(out, query, scopes, classes, since, until, limit)
            out.sort(key=lambda x: -x["score"])
        if self.expose_step > 0.0 and self.expose_window > 0:
            # EXPOSURE-DECAY (see __init__): demote what the recent ledger already showed, AFTER
            # fusion (so it demotes the final claim to attention, whichever arm voted it there)
            # and BEFORE dedup (so a demoted twin loses the collapse to its fresher sibling).
            dem = self._exposure()
            if dem:
                for h in out:
                    e = dem.get(h["id"])
                    if e:
                        h["score"] = max(0.0, round(h["score"] - self.expose_step * min(e, self.expose_cap), 6))
                out.sort(key=lambda x: -x["score"])
        if self.dedup_recall:
            out = self._dedupe(out)
        hits = out[:limit]
        if hits and credit:
            self._seen([h["id"] for h in hits])
        return hits

    def _vec_matrix(self):
        """(ids, mat, scope, class, created, updated, conf) for every ACTIVE row that carries a
        vector, cached; rebuilt when a write dirtied it OR the embedded-count moved (a backfill ran
        beside a live server - the count check is what notices without a restart). At this store's
        scale a full cosine scan is the honest design: ~1 ms per 2.5k rows measured at build, so an
        ANN index would be more machinery than math; a 100k-row store is the point to revisit, and
        stats() will have said so long before."""
        import numpy as np
        with self.lock:
            n = self.conn.execute("select count(*) from embeddings").fetchone()[0]
            if self._vmeta is not None and not self._vdirty and n == self._ecount:
                self.conn.commit()
                return self._vmeta
            rows = self.conn.execute(
                "select m.id, e.vec, m.scope, coalesce(m.class,''),"
                " extract(epoch from m.created_at), extract(epoch from m.updated_at), m.confidence"
                " from embeddings e join memories m on m.id = e.id where m.invalid_at is null"
                " order by m.id").fetchall()
            self.conn.commit()
        if not rows:
            self._vmeta, self._vdirty, self._ecount = None, False, n
            return None
        mat = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
        meta = {"ids": np.array([r[0] for r in rows]), "mat": mat,
                "ix": {int(r[0]): n for n, r in enumerate(rows)},   # id -> row, for the fusion pass
                "scope": np.array([r[2] for r in rows], dtype="U24"),
                "class": np.array([r[3] for r in rows], dtype="U48"),   # U24 silently truncated deep marks
                "created": np.array([r[4] for r in rows], dtype=np.float64),
                "updated": np.array([r[5] for r in rows], dtype=np.float64),
                "conf": np.array([r[6] for r in rows], dtype=np.float32)}
        self._vmeta, self._vdirty, self._ecount = meta, False, n
        return meta

    def _scope_vecs(self, scope: str) -> dict:
        """{row id: vector} for the ACTIVE rows of ONE scope, cached against that scope's own stamp
        (count, max id, max updated_at). The write-time near-dupe guard compares a new fact only
        with its own scope - the same boundary exact-text dedup uses - so that is all it needs, and
        taking the whole-store matrix for the job made every write pay a store-sized rebuild:
        measured 2026-10-14, ingest ran 5.5 rows/s at ~10k rows (180 ms per write) against 7 ms at
        2,589, and the profile said the matrix was ~5% of that while the token table was 86% - both
        rebuilt once per write, both linear in the store, so the pair was quadratic. The read path
        keeps the whole-store matrix, because a query really does span every scope. Entries are kept
        for the 32 most recently written scopes: a venue ingests 500 of them and the cache must not
        grow with the benchmark."""
        import numpy as np
        with self.lock:
            key = self.conn.execute(
                "select count(*), coalesce(max(id),0), coalesce(extract(epoch from max(updated_at)), 0.0)"
                " from memories where scope = %s and invalid_at is null", (scope,)).fetchone()
            hit = self._svec.get(scope)
            if hit is not None and hit[0] == tuple(key):
                return hit[1]
            rows = self.conn.execute(
                "select m.id, e.vec from embeddings e join memories m on m.id = e.id"
                " where m.scope = %s and m.invalid_at is null", (scope,)).fetchall()
            self.conn.commit()
        vecs = {int(rid): np.frombuffer(blob, dtype=np.float32) for rid, blob in rows}
        self._svec[scope] = (tuple(key), vecs)
        self._svec.move_to_end(scope)
        while len(self._svec) > 32:
            self._svec.popitem(last=False)
        return vecs

    def _embed_query_cached(self, query: str):
        key = norm_text(query)
        hit = self._qc.get(key)
        if hit is not None:
            self._qc.move_to_end(key)
            return hit
        try:
            vec = self.embedder.embed_query(query)
        except Exception:
            return None                                   # a broken query embed degrades to lexical
        self._qc[key] = vec
        while len(self._qc) > 128:
            self._qc.popitem(last=False)
        return vec

    def _semantic(self, cands: list[dict], query: str, scopes, classes,
                  since, until, limit: int) -> list[dict]:
        """PHASE 4 hybrid pass: bm25 candidates UNION the cosine top over every embedded active row.
        The UNION is the point - the temporal diagnostic (spikes/lme/TEMPORAL_DIAG.md) showed
        multi-evidence questions whose remaining events FTS never proposes because they share no
        rare word with the question; cosine finds them. The two lists merge by reciprocal-rank
        fusion, NOT by mixing their score scales - the live-store reach probe (tools/m4_reach.py)
        is the measurement behind that choice, spelled out where the fusion runs. Embedder OFF
        never reaches this line: the shipped ranking stays byte-identical by construction."""
        import numpy as np
        meta = self._vec_matrix()
        if meta is None:
            return cands
        qv = self._embed_query_cached(query)
        if qv is None or qv.shape != meta["mat"].shape[1:]:
            return cands
        mask = np.isin(meta["scope"], list(scopes))
        if classes:
            mask &= shelf_mask(classes, meta["class"])
        if since is not None:
            mask &= meta["created"] >= float(since)
        if until is not None:
            mask &= meta["created"] <= float(until)
        if not mask.any():
            return cands
        cos = meta["mat"] @ qv
        order = np.argsort(-np.where(mask, cos, np.float32(-2.0)))[:limit * 3]
        # RANK fusion (RRF, constant in serve.memory.RRF_K - swept and set by measurement, see that
        # constant's note): each list votes by POSITION, never by its
        # own score scale. The first version of this pass blended raw cosine into the bm25 blend at
        # 0.55/0.20, and the reach probe on the live store (tools/m4_reach.py, 2,520 rows, 2026-10-14)
        # caught what that does: a rank-1 exact-lexical answer fell to 14th, because a store that size
        # holds dozens of rows at cos 0.5-0.6 on any topical query. Fusing ranks keeps a lexical first
        # place first and still lifts what BOTH lists want - the coverage the temporal diagnostic
        # asked for, without paying for it in precision. Recency and confidence survive as
        # TIE-BREAKERS only: their terms total at most 1.1e-5, and the RRF gap between adjacent
        # ranks stays above that through rank 241 (at rank 120 the step is 1/(180*181) = 3.1e-5),
        # so across every limit the clamp allows (<= 240) they break ties but never cross a rank
        # step. Deeper than that the epsilon can outvote one step - rows that far down are never
        # shown, and there the difference is noise either way. Rows
        # without vectors (a store that never ran tools/m4_embed.py) get the lexical term alone with
        # neutral tie-breakers - recallable, just never promoted by a vote it cannot cast.
        rrf_k = RRF_K   # the shipped default; probes sweep the module constant
        lex_rank = {c["id"]: i + 1 for i, c in enumerate(cands)}
        by_id = {c["id"]: c for c in cands}
        now = time.time()
        cos_rank: dict[int, int] = {}
        picked: list[int] = []
        for cr, j in enumerate(order, start=1):
            if not mask[j]:
                continue
            if self.floor_cosine > 0.0 and float(cos[j]) < self.floor_cosine:
                # below the floor the cosine arm casts NO vote at all: not a pick of its own,
                # and no cos_rank boost for a lexical candidate it does not believe in either.
                # (The floor is the hub-row gate - see the __init__ note; 0.0 never lands here.)
                continue
            rid = int(meta["ids"][j])
            cos_rank[rid] = cr
            if rid not in by_id:
                picked.append(rid)

        def fused(lr: int | None, cr: int | None, upd: float, conf: float) -> float:
            v = (1.0 / (rrf_k + lr) if lr else 0.0) + (1.0 / (rrf_k + cr) if cr else 0.0)
            fresh = math.exp(-max(0.0, now - float(upd)) / 86400.0 / AGE_SCALE_DAYS)
            return round(v + 1e-5 * fresh + 1e-6 * float(conf), 6)

        for c in cands:
            j = meta["ix"].get(c["id"])
            lr, cr = lex_rank[c["id"]], cos_rank.get(c["id"])
            c["cos_rank"] = cr                    # provenance, not plumbing: which arm put a row where
            upd, conf = (now, 0.8) if j is None else (float(meta["updated"][j]), float(meta["conf"][j]))
            c["score"] = fused(lr, cr, upd, conf)
        if picked:
            with self.lock:
                got = {r[0]: r for r in self.conn.execute(
                    "select id, scope, text, coalesce(class,''), confidence, coalesce(hits, 0) "
                    "from memories where id in ("
                    + ",".join(["%s"] * len(picked)) + ")", picked).fetchall()}
                self.conn.commit()
            for rid in picked:
                r = got.get(rid)
                if r is None:
                    continue                            # the row went away between scan and fetch
                j = meta["ix"][rid]                     # picked rows came from the cosine list: they have vectors
                cands.append(dict(id=rid, scope=r[1], text=r[2],
                                  score=fused(None, cos_rank.get(rid),
                                              float(meta["updated"][j]), float(meta["conf"][j])),
                                  cos_rank=cos_rank.get(rid),     # the cosine arm found it, bm25 never did
                                  lex_rank=None,
                                  confidence=r[4], hits=r[5],     # same promise as the lexical arm above
                                  **{"class": r[3]}))
        return cands

    def _exposure(self) -> dict:
        """{row id: exposures} across the newest expose_window journaled turns - the exposure
        ledger (turns.injected: the ids each turn's block ACTUALLY showed, written by the
        injection path, not what recall merely proposed). Cached against the turns stamp
        (count, max id) so a steady conversation pays one scan; reading the ledger never
        touches hits/last_seen_at - exposure demotes recall, it does not feed the feedback
        counter back on itself (the pin-cache law, applied to the new input)."""
        with self.lock:
            key = self.conn.execute("select count(*), coalesce(max(id),0) from turns").fetchone()
            if self._expo is not None and self._expo[0] == tuple(key):
                self.conn.commit()
                return self._expo[1]
            rows = self.conn.execute("select injected from turns order by id desc limit %s",
                                     (max(1, self.expose_window),)).fetchall()
            self.conn.commit()
        dem: dict = {}
        for (inj,) in rows:
            if not inj:
                continue
            if isinstance(inj, dict):
                ids = inj.get("ids", [])
            elif isinstance(inj, list):
                ids = inj                       # jsonb returns the ledger natively - no parse, no wire
            else:
                try:
                    ids = json.loads(inj)       # a TEXT-era value (imported store): still readable
                except (TypeError, ValueError):
                    continue
            for i in ids:
                try:
                    dem[int(i)] = dem.get(int(i), 0) + 1
                except (TypeError, ValueError):
                    continue
        self._expo = (tuple(key), dem)
        return dem

    def _seen(self, ids: list[int]) -> None:
        now = self._dt(time.time())
        with self.lock:
            self._execmany("update memories set hits=hits+1, last_seen_at=%s where id=%s",
                           [(now, i) for i in ids])
            self.conn.commit()

    def _render_block(self, hits: list[dict], budget: int, scope: str) -> tuple[str, list[dict]]:
        """One fact block, byte-shaped identically for head and tail use: header + instruction line +
        one "[n] (scope) text" line per fact, kept under `budget` characters. Returns the block and the
        hits it ACTUALLY shows - what a later request dedups against and what the journal credits must
        be the lines the model really read, not everything the search returned. The header carries
        "shown X of N active" (measured need, web venue 2026-10-12: with a rotating retrieval sample
        the model cannot tell a full view from a slice, and recites either with equal confidence -
        one honest number in the header turns 'I recall' into 'I was shown this')."""
        with self.lock:
            n_active = self.conn.execute(
                "select count(*) from memories where invalid_at is null").fetchone()[0]
            self.conn.commit()
        v, instruction = self.version(), (
            "Long-term memory of this user, kept by the inference server and shared by all their"
            " applications. Use it silently when relevant - never mention these notes, this tag, or"
            " 'remembering' to the user; if a note conflicts with what the user says now, they win.")
        def head(n: int) -> str:      # the count is written AFTER trimming: shown = what the model reads
            return f'<server_memory version="{v}" scope="{scope}" shown="{n} of {n_active} active">'
        lines = [head(len(hits)), instruction]
        lines += [f"[{i + 1}] ({h['scope']}) {h['text']}" for i, h in enumerate(hits)]
        lines.append("</server_memory>")
        trimmed, used = [], len(lines[0]) + len(lines[-1]) + 120          # the header and the instruction line cost
        shown = []
        for h, line in zip(hits, lines[2:-1]):
            sc = h.get("score")
            if self.block_min_score > 0.0 and sc is not None and sc < self.block_min_score:
                break       # CAP NOT QUOTA (m4_blockrule, 2026-10-16): the list is score-
                # descending, so the first miss ends the block - everything after is further
                # below. A hit without a score passes: the floor cannot gate what it cannot
                # read, and silently emptying a pinned block would be a worse bug than the
                # stuffing this replaces.
            used += len(line) + 1
            if used > budget:
                break
            trimmed.append(line)
            shown.append(h)
        return "\n".join([head(len(shown)), instruction] + trimmed + [lines[-1]]), shown

    def _route(self, query: str, scope: str) -> list[str] | None:
        """The query->shelf router for the AUTO-INJECTION path (see shelf_vote for the law, the
        measured role, and why contamination is the only job it ever had). Runs only where nobody
        addressed anything - an explicit classes= is the caller's decision and always wins, the
        same law that made purity 1.000 at every shelf. One cheap bm25-only pass (no embedder ask,
        no LLM, no _seen credit), then the rank-weighted shelf vote. None = no signal, stay
        unscoped, byte-identical to the old path. The pick is RETURNED, never applied here: each
        caller knows its own fallback law (see block_for / turn_block_for)."""
        if not self.route_shelves:
            return None
        self._route_stats["tries"] += 1
        stage1 = self.search(query, scope=scope, limit=ROUTE_STAGE1, semantic=False, credit=False)
        pick = shelf_vote(stage1)
        if pick:
            self._route_stats["picks"] += 1
        return pick or None

    def block_for(self, messages: list[dict], scope: str,
                  classes: list[str] | None = None,
                  semantic: bool = True) -> tuple[str | None, list[dict], str]:
        """The hidden system block for this conversation - or (None, [], "") when there is no user text to
        recall with. Pinned per (scope, classes, first user message): byte-stable while cached, fresh
        bytes for a new conversation or a different shelf request - a block pinned for a ["STR"]
        conversation must never answer an ["EVAL"] one. The query is the last user message; the search
        also uses the first one when the conversation's newest text is a tool result. The returned hits
        are the facts the block SHOWS (id-only dicts once pinned) - per-turn fresh recall is
        `turn_block_for`, not this."""
        users = [text_of(m.get("content")) for m in messages if isinstance(m, dict) and m.get("role") == "user"]
        users = [u for u in users if u.strip()]
        if not users:
            return None, [], ""
        scope = scope_name(scope)
        shelf = shelf_key(classes)      # see shelf_key: a bad address must not share the unscoped pin
        # The ARM rides the pin key, exactly as the shelf does: a block built by the LEXICAL arm must
        # never answer the embedder arm's identical question (and vice versa), or the arm-C A/B would
        # measure the same bytes twice and report a difference that is not there. Stores with no
        # embedder configured keep the old key byte-for-byte - the marker only exists where the choice
        # exists.
        arm = "" if self.embedder is None else ("|s" if semantic else "|l")
        key = (scope, "h" + hashlib.sha1(
            (norm_text(users[0]) + "|" + shelf + arm).encode("utf-8", "replace")).hexdigest()[:16])
        query = users[-1][:400] if len(users[-1].strip()) > 3 else users[0][:400]
        pinned = self._blocks.get(key)
        if pinned is not None:
            self._blocks.move_to_end(key)
            block, ids = pinned
            return block, [{"id": i} for i in ids], query
        want = classes if classes is not None else self._route(query, scope)
        #  ^ 2026-10-17, the shelf router: only an UNADDRESSED request gets routed. The pick does NOT
        # ride the pin key - it is a property of the block (computed once at miss, sticky like the
        # facts themselves), not a property of the request; two conversations with the same first
        # message and no header can never disagree on a pick within one store state.
        hits = self.search(query, scope=scope, classes=want, semantic=semantic)
        if classes is None and want and not hits:
            # FAIL OPEN (2026-10-17): the wrong pick hides the gold ENTIRELY - that is routing's
            # measured downside (docs/MEMORY_CORE.md, "Shelf isolation"). A pick that finds NOTHING
            # therefore falls back to the unscoped search the store ran before routing existed, which
            # can only ADD what the old path would have shown: without the fallback this request
            # would have injected NO bytes at all. It cannot undo the leak cut on blocks that DID
            # fill, and a caller that named a shelf gets no fallback - its address holds what it
            # holds, fail closed, exactly as the 2026-10-15 bad-address law decided.
            self._route_stats["fallbacks"] += 1
            hits = self.search(query, scope=scope, classes=None, semantic=semantic)
        if not hits:
            return None, [], query
        block, shown = self._render_block(hits, self.max_block_chars, scope)
        self._blocks[key] = (block, [h["id"] for h in shown])
        while len(self._blocks) > self._cache_entries:
            self._blocks.popitem(last=False)
        return block, shown, query

    def turn_block_for(self, messages: list[dict], scope: str,
                       exclude_ids: list[int] | tuple[int, ...] = (),
                       classes: list[str] | None = None, semantic: bool = True):
        """Fresh recall for the conversation's NEWEST user message, minus the facts the pinned head
        block already carries -> (block | None, shown hits). It is merged into that message rather than
        the system head (see request_state): a 184k-token agent session reuses ~99.9% of its prompt
        bytes every turn (dev log: "prompt 183,987 tokens = 183,812 reused + 175 read"), so touching
        the head costs a full re-read of the conversation, while the tail is re-sent anyway. The
        conversation's first turn gets no tail - the head block was computed from that same message -
        and a request whose newest text is too short to query with gets none either. Cached like the
        head block, keyed by (scope, newest message, the exclusion set), so a retried turn replays
        byte-identically."""
        if not self.per_turn:
            return None, []
        users = [text_of(m.get("content")) for m in messages if isinstance(m, dict) and m.get("role") == "user"]
        users = [u for u in users if u.strip()]
        if len(users) < 2 or len(users[-1].strip()) <= 3:
            return None, []
        scope = scope_name(scope)
        seen = sorted(set(int(i) for i in exclude_ids or ()))
        shelf = shelf_key(classes)      # see shelf_key: a bad address must not share the unscoped pin
        arm = "" if self.embedder is None else ("|s" if semantic else "|l")   # see block_for: the arm
        material = f"{norm_text(users[-1][:400])}|{','.join(map(str, seen))}|{shelf}" + arm
        key = (scope, "t" + hashlib.sha1(material.encode("utf-8", "replace")).hexdigest()[:16])
        pinned = self._blocks.get(key)
        if pinned is not None:
            self._blocks.move_to_end(key)
            block, ids = pinned
            return (block or None), [{"id": i} for i in ids]
        q = users[-1][:400]
        want = classes if classes is not None else self._route(q, scope)   # see block_for: the router
        hits = [h for h in self.search(q, scope=scope, classes=want, semantic=semantic)
                if h["id"] not in set(seen)]
        if classes is None and want and not hits:
            # FAIL OPEN, the same law as block_for's (see there): a tail pick that finds nothing NEW
            # falls back to unscoped recall rather than pinning an empty tail for this message.
            self._route_stats["fallbacks"] += 1
            hits = [h for h in self.search(q, scope=scope, classes=None, semantic=semantic)
                    if h["id"] not in set(seen)]
        hits = hits[:self.turn_recall_limit]
        if not hits:
            self._blocks[key] = ("", [])            # the miss is pinned too: same message, same nothing
            return None, []
        block, shown = self._render_block(hits, self.turn_block_chars, scope)
        if not shown:
            self._blocks[key] = ("", [])            # nothing fits the budget: inject no bytes at all
            return None, []
        self._blocks[key] = (block, [h["id"] for h in shown])
        while len(self._blocks) > self._cache_entries:
            self._blocks.popitem(last=False)
        return block, shown

    # ---- journal (raw turns; the curation loop reads these) -------------------

    def journal_turn(self, scope: str, user_text: str, assistant_text: str, model: str | None = None,
                     injected: list[int] | None = None) -> int:
        """Store one completed turn for curation to read later. `injected` is the list of memory row ids
        the server actually showed on that turn (ctx["hits"] from request_state). NULL and [] are
        different facts and are kept different: NULL means the caller did not know - an API journal write,
        or any turn before this column existed - while [] means recall ran and showed nothing. Without
        that distinction the self-feeding question ("did this fact come from memory the server itself
        supplied?") cannot be asked, which is the whole reason tools/m4_selffeed.py had to fall back on
        word overlap and called 42.6% of curated facts unmatchable.

        Same user_text as the newest journaled row in this scope = the NEXT ROUND of one multi-call
        agent turn, not a new exchange (measured 2026-10-17: one OpenCode turn journaled five rows
        with identical user_text, and the extractor read the same content five times and minted
        five paraphrased memories - the duplicate clusters a manual cleanup retired that day were
        exactly that shape). UPDATE the row instead of appending: the newest round carries the
        real answer where the earlier rounds were tool calls, and re-queue it (processed=0) so
        curation reads the settled answer - the sweep's law arm (tools/curate_loop.py) is what
        keeps the re-read from duplicating. A genuinely repeated identical message keeps ONE row:
        the dedup law the store already lives by, applied at the source."""
        with self.lock:
            sc = scope_name(scope)
            inj = (None if injected is None
                   else Jsonb(sorted({int(i) for i in injected if str(i).strip() != ""})))
            last = self.conn.execute("select id, user_text, processed from turns where scope=%s"
                                     " order by id desc limit 1", (sc,)).fetchone()
            if last and norm_text(last[1]) == norm_text(user_text):
                self.conn.execute(
                    "update turns set assistant_text=%s, model=%s, created_at=%s, injected=%s,"
                    " processed=0 where id=%s",
                    (assistant_text[:16000], (model or "")[:80] or None,
                     self._dt(time.time()), inj, last[0]))
                self.conn.commit()
                self._touched()
                return last[0]
            new_id = self.conn.execute(
                "insert into turns(scope,user_text,assistant_text,model,created_at,injected)"
                " values(%s,%s,%s,%s,%s,%s) returning id",
                (sc, user_text[:8000], assistant_text[:16000],
                 (model or "")[:80] or None, self._dt(time.time()), inj)).fetchone()[0]
            self.conn.commit()
            self._touched()
            return new_id

    def pending_turns(self, limit: int = 200) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "select id,scope,user_text,assistant_text,created_at,injected from turns where processed=0"
                " order by id limit %s", (max(1, min(1000, int(limit))),)).fetchall()
            self.conn.commit()

        def _inj(v):
            """jsonb returns the ledger list natively; a TEXT-era value (an imported pre-jsonb row
            that arrives as str) still parses, and None stays None - the NULL-vs-empty law above."""
            if v is None:
                return None
            return json.loads(v) if isinstance(v, str) else list(v)
        return [dict(id=r[0], scope=r[1], user=r[2][:2000], assistant=r[3][:4000],
                     created_at=round(self._ep(r[4]), 1),
                     # None = the caller never told us (a turn journaled before the column, or an API
                     # write); [] = recall ran and showed nothing. The curation loop grounds the
                     # extractor on created_at already and can use this the same way.
                     injected=_inj(r[5]))
                    for r in rows]

    def mark_processed(self, ids: list[int]) -> None:
        if not ids:
            return
        with self.lock:
            self._execmany("update turns set processed=1 where id = %s",
                           [(int(i),) for i in ids])
            self.conn.commit()
            self._touched()

    # ---- stats ----------------------------------------------------------------

    def stats(self) -> dict:
        with self.lock:
            memories = self.conn.execute("select count(*) from memories where invalid_at is null").fetchone()[0]
            by_scope = dict(self.conn.execute(
                "select scope, count(*) from memories where invalid_at is null group by scope").fetchall())
            by_class = {(r[0] or "unclassified"): r[1] for r in self.conn.execute(
                "select class, count(*) from memories where invalid_at is null group by class").fetchall()}
            invalid = self.conn.execute("select count(*) from memories where invalid_at is not null").fetchone()[0]
            # counted live, not from the cached vector matrix: that is only as fresh as the last
            # recall, which is how stats() briefly reported 2,594 vectors over a 2,538-row store
            # right after a merge.
            embedded = self.conn.execute(
                "select count(*) from embeddings e join memories m on m.id = e.id"
                " where m.invalid_at is null").fetchone()[0]
            pending = self.conn.execute("select count(*) from turns where processed=0").fetchone()[0]
            turns = self.conn.execute("select count(*) from turns").fetchone()[0]
            self.conn.commit()
        return {"version": self.version(), "memories": memories, "by_scope": by_scope,
                "by_class": by_class,
                "turns": turns, "turns_pending": pending, "inject": self.inject,
                "max_memories": self.max_memories, "invalid": invalid,
                "dedup_recall": self.dedup_recall,
                "at_rest": ("off" if not self.encrypt else {
                    "mode": "pgdata-0700 + key-file" + ("+passphrase-env" if self.passphrase_env else ""),
                    "key_file": self.key_path,
                    "cluster": "own" if self._srv is not None else "attached",
                    "data": self.pgdata if self._srv is not None else self._dbname,
                    # the honest sentence ADR-002 ships in place of the old seal law: a RUNNING
                    # store is readable by whoever owns the machine; a stopped one is only as
                    # private as its 0700 data dir; the CIPHERTEXT line of the story is the
                    # backup - encrypted pg_dump under this same key-file (the R6 tool).
                    "running_is_plaintext": True,
                    "backups": "encrypted pg_dump - tools/m6_backup.py (round trip verified "
                               "2026-10-16: live-store backup 1.7 s, restore byte-verified by "
                               "identical lexical rankings)"}),
                "pressure": bool(self.max_memories) and memories >= 0.8 * self.max_memories,
                "recall_limit": self.recall_limit, "max_block_chars": self.max_block_chars,
                "per_turn": self.per_turn, "turn_recall_limit": self.turn_recall_limit,
                "turn_block_chars": self.turn_block_chars,
                "pinned_blocks": len(self._blocks),
                # the router's own ledger (2026-10-17): tries = unaddressed blocks that asked for a
                # pick, picks = votes that named >=1 shelf, fallbacks = picks that found NOTHING and
                # fail-opened to unscoped. picks/tries is not accuracy (that needs a gold shelf) - it
                # is the "did the router actually run, and how often does it abstain/hit the floor"
                # pair the venue replay reads beside the leak numbers.
                "router": {"on": self.route_shelves, **self._route_stats},
                "embedder": ({"on": True, "loaded": True,
                              "model": getattr(self.embedder, "model_name", type(self.embedder).__name__),
                              "vectors": embedded, "active": memories,
                              "unembedded": max(0, memories - embedded)}   # tools/m4_embed.py closes the gap
                             if self.embedder is not None else
                             ({"on": True, "loaded": False, "error": self.embedder_error}
                              if self.embedder_error else {"on": False})),
                "db": self.db_path,
                "dir": self.directory}    # the store dir - what tools/attach() needs (the tools'
                                          # native twin of the old stats.db file path)

    def close(self) -> None:
        """Close the store connection, then release the cluster handle. The library's own law,
        measured in ADR-002 R0b: a process that ATTACHED to a running cluster does not stop it
        when it leaves - only the starter's cleanup stops it - which is what lets the curation
        tool and the venue harnesses read a live store without tripping the server over. Within
        ONE process the rule is the older one: one owner instance per store dir (production has
        exactly one; tests run in pg_uri mode and own no cluster at all)."""
        with self.lock:
            try:
                self.conn.close()
            except Exception:                        # noqa: BLE001 - close is best-effort by law
                pass
        if self._srv is not None:
            srv, self._srv = self._srv, None
            try:
                srv.cleanup()
            except Exception:                        # noqa: BLE001
                pass

    # ---- at-rest key material (ADR-002 retired the seal/reveal FILE mechanics - they were
    # one-file rules and a Postgres data dir is many files. What stays is the 0600 key-file
    # beside the store, because the ENCRYPTED pg_dump backup path reads it - and it has been
    # read since the same day: the stdlib cipher that sealed the old single file (HMAC-SHA256
    # CTR, encrypt-then-MAC) returned as tools/m6_backup.py, the SMEM1 format reborn as SDMP1,
    # round-trip verified live 2026-10-16 (backup of a mid-replay venue store 1.7 s; restore
    # into a fresh dir searched byte-identical). The passphrase stretch rides --pass-env;
    # the FIDO2 seal idea stays parked. A stopped cluster is only as private as its data dir,
    # and its backups are ciphertext. ----

    def _key_bytes(self) -> bytes:
        """The 32-byte master, created 0600 on first use. O_EXCL closes the create race; a loser
        simply reads what the winner wrote."""
        if not os.path.exists(self.key_path):
            try:
                fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, os.urandom(32))
                finally:
                    os.close(fd)
            except FileExistsError:
                pass
        with open(self.key_path, "rb") as f:
            key = f.read()
        if len(key) != 32:
            raise ValueError(f"memory key-file {self.key_path} is {len(key)} bytes, expected 32")
        return key

    def _touched(self) -> None:
        """One more committed write. The SQLite era sealed a snapshot every 50th write so a power
        loss lost at most 49; the cluster's own WAL and checkpoints own durability now, so this
        is the counter only - kept because the write paths and stats read it."""
        self._writes += 1


# ---- server glue ---------------------------------------------------------------

def request_state(mem: "MemoryService | None", messages: list[dict], headers, req: dict) -> tuple[list[dict], dict | None]:
    """/v1/chat/completions' memory step: (messages, ctx). With memory off or muted
    (`X-Strata-Memory: off`), the messages come back untouched and ctx is None -
    the byte-identical upstream path. With nothing recalled, the messages still come back
    untouched but ctx is built (unless the request has no user text): the exchange journals
    anyway, which is how a fresh scope bootstraps. Otherwise a hidden system block is
    prepended (pinned per
    conversation), the conversation's newest user message may carry a fresh per-turn block of facts the
    head does not have (see turn_block_for), and ctx carries what to journal when the answer completes.
    The caller's project scope is `X-Strata-Memory-Scope`."""
    if mem is None:
        return messages, None
    if (headers.get("X-Strata-Memory") or "").strip().lower() in ("off", "mute", "0"):
        return messages, None
    scope = scope_name(headers.get("X-Strata-Memory-Scope"))
    # the class axis (PHASE 4.5): "X-Strata-Memory-Class: STR,USR" pulls engine talk off the eval
    # corpus. Junk prefixes are dropped quietly (a header may carry anything, and a half-valid list
    # still filters); NOTHING in the header means every fact stays eligible, byte-identical to the
    # pre-class store - the filter only ever narrows what a request that asks for a shelf sees. An
    # ALL-junk header is the one case that changed (2026-10-15): it used to mean "no filter" and show
    # the whole store, and now means "an address that names no shelf" and shows nothing - see class_list.
    raw = [x.strip() for x in (headers.get("X-Strata-Memory-Class") or "").split(",") if x.strip()]
    classes = class_list(raw) if raw else None      # an absent header stays None: byte-identical
    # The per-request embedder mute (arm-C A/B): the SAME store, the SAME vector set, one boot - only
    # the ranking differs. Absent header, absent embedder: nothing changes anywhere.
    semantic = (headers.get("X-Strata-Memory-Embedder") or "").strip().lower() not in ("off", "mute", "0")
    block, hits, query = mem.block_for(messages, scope, classes, semantic=semantic)
    users = [text_of(m.get("content")) for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    last_user = next((u for u in reversed(users) if u.strip()), "")
    if block is None:
        # journal even when nothing was recalled: a quiet turn today is tomorrow's curation source,
        # and a scope with no facts yet could never bootstrap otherwise. No bytes join the prompt -
        # only ctx is built, and only when there is a user turn worth curating (a tool-continuation
        # request with no user text journals an answer nobody asked for: skip it).
        if not last_user.strip():
            return messages, None
        return messages, {"scope": scope, "user": last_user, "model": req.get("model"), "query": query,
                          "classes": classes, "hits": []}
    tblock, thits = mem.turn_block_for(messages, scope, [h["id"] for h in hits], classes,
                                       semantic=semantic)
    ctx = {"scope": scope, "user": last_user, "model": req.get("model"), "query": query,
           "classes": classes, "hits": [h["id"] for h in hits] + [h["id"] for h in thits]}
    # The Qwen chat template allows ONE system message, at index 0 ("System message must be at the
    # beginning"), and openai_to_messages has already folded "developer" into system and turned late
    # systems into user - so the block cannot ride as a second system turn. When the client sent its
    # own leading system (OpenCode, Claude Code, ...), merge ours in front of ITS text instead of
    # prepending: the merged head keeps the block's stable bytes at the very front of the prompt,
    # the template sees exactly one system, and the client's instruction is untouched in substance.
    head = messages[0] if messages and isinstance(messages[0], dict) else None
    if head is not None and head.get("role") in ("system", "developer"):
        c = head.get("content")
        if isinstance(c, list):
            merged = dict(head, content=[{"type": "text", "text": block}] + list(c))
        elif isinstance(c, str) and c.strip():
            merged = dict(head, content=f"{block}\n\n{c}")
        else:
            merged = dict(head, content=block)
        out = [merged] + list(messages[1:])
    else:
        out = [{"role": "system", "content": block}] + list(messages)
    # The per-turn block rides the tail, NOT the head: merged into the newest user message, whose bytes
    # are new to the prompt this turn anyway, so the conversation's reused prefix keeps every checkpoint
    # byte it had. The template's one-system rule is untouched - this rides inside a user turn.
    if tblock is not None:
        i = next((j for j in range(len(out) - 1, -1, -1)
                  if isinstance(out[j], dict) and out[j].get("role") == "user"
                  and text_of(out[j].get("content")).strip()), None)
        if i is not None:
            c = out[i].get("content")
            if isinstance(c, list):
                out[i] = dict(out[i], content=[{"type": "text", "text": tblock}] + list(c))
            else:
                out[i] = dict(out[i], content=f"{tblock}\n\n{c}")
    return out, ctx


def journal_chunks(chunks, mem: "MemoryService", ctx: dict):
    """Wrap the OpenAI chunk generator to collect the answer's text and journal it as one turn. Transparent:
    every item passes through unchanged and on the same schedule; journaling rides in finally, and a store
    error never breaks an answer the user already watched."""
    parts: list[str] = []
    try:
        for c in chunks:
            if isinstance(c, dict):
                try:
                    delta = (c.get("choices") or [{}])[0].get("delta") or {}
                    if isinstance(delta.get("content"), str):
                        parts.append(delta["content"])
                except (KeyError, IndexError, TypeError, AttributeError):
                    pass
            yield c
    finally:
        text = "".join(parts).strip()
        if text:
            try:
                mem.journal_turn(ctx["scope"], ctx["user"], text, ctx.get("model"),
                                 ctx.get("hits"))
            except Exception:      # noqa: BLE001 - the journal is never allowed to break a reply
                pass


def journal_stream(items, mem: "MemoryService", ctx: dict):
    """The dialect twin of journal_chunks (beta item B, 2026-10-12): wraps the Anthropic event
    stream (items are (name, event) tuples, keepalives None) or the Responses event stream
    (items are dicts) and journals the completed answer. Text comes from the deltas each API
    defines - content_block_delta/text_delta for Anthropic, response.output_text.delta for
    Responses; thinking_delta and reasoning deltas are deliberately NOT collected: the answer
    is the turn, the rumination is not. Transparent like its twin: every item passes through
    unchanged on the same schedule, journaling rides in finally, and a store error never
    breaks an answer the user already watched."""
    parts: list[str] = []
    try:
        for item in items:
            e = item[1] if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], dict) \
                else (item if isinstance(item, dict) else None)
            if e is not None:
                t = e.get("type")
                if t == "content_block_delta":
                    d = e.get("delta") or {}
                    if d.get("type") == "text_delta" and isinstance(d.get("text"), str):
                        parts.append(d["text"])
                elif t == "response.output_text.delta" and isinstance(e.get("delta"), str):
                    parts.append(e["delta"])
            yield item
    finally:
        text = "".join(parts).strip()
        if text:
            try:
                mem.journal_turn(ctx["scope"], ctx["user"], text, ctx.get("model"),
                                 ctx.get("hits"))
            except Exception:      # noqa: BLE001 - the journal is never allowed to break a reply
                pass
