# Postgres Build: the code audit and research loop (2026-10-16)

The loop ADR-001 requires before P1. The audit inventories exactly what the seam must abstract
(with file:line evidence), the research checks the build's load-bearing assumptions against the
2026 state of the ecosystem (sources cited; anything cited is RESEARCHED, not measured here), and
section 3 states the shape that falls out of both. ADR-001 stays the decision record; this is the
build spec it feeds.

## 1. The audit: the storage surface, measured from the tree

`serve/memory.py` holds every SQL statement in the product: **58 statements at 63 call sites**
(34 select, 8 update, 8 insert, 4 delete, 4 alter, plus one `executescript` DDL block). All of
them sit behind one connection (`self.conn`, `serve/memory.py:450`) serialized under one lock -
the server's request threads never touch SQLite concurrently. That single-owner shape is why a
seam is tractable at all.

Schema (`memory.py:455-504`): five tables (`memories`, `shelves`, `turns`, `meta`, `embeddings`),
one FTS5 virtual table (`mem_fts`, external-content over `memories`) kept in step by three
triggers, no secondary indexes. Migrations are runtime `ALTER`s discovered through
`pragma table_info` (`memory.py:512-530`): `invalid_at`, `superseded_by`, `class`, `injected`.

The SQLite-isms, grouped by what the seam must do about each:

| # | coupling | where | seam answer |
|---|---|---|---|
| 1 | connection model: `check_same_thread=False`, `PRAGMA journal_mode=WAL / synchronous=NORMAL / busy_timeout=5000` | `450-454` | per-backend connect; pragmas stay SQLite-only |
| 2 | FTS5: `tokenize='porter unicode61 remove_diacritics 1'`, external content + 3 triggers, `" OR "`-joined quoted words (max `MAX_QUERY_WORDS=12`), `order by bm25(mem_fts)`, `limit*3` candidates | `485-499`, `1127-1155` | a match-builder + order-by clause per backend (§3); parity battery in P2 |
| 3 | row identity: `INTEGER PRIMARY KEY AUTOINCREMENT`, `insert or ignore`, `lastrowid` | `457`, `477`, `581`, `719` | IDENTITY / `ON CONFLICT DO NOTHING` / `RETURNING id` |
| 4 | migration discovery: `pragma table_info` | `512`, `528` | `information_schema.columns` on PG |
| 5 | time: `REAL` epoch seconds from `time.time()`, rounded to 0.1 at the API boundary | schema, `578`, `910` | `double precision` keeps the math byte-identical; `timestamptz` is a P5 upgrade, not a P1 need |
| 6 | vectors: `embeddings(id, vec BLOB)` float32 - but the READ path is storage-independent: the whole matrix pins into numpy and the cosine is an exact in-memory scan | `503-504`, `_vec_matrix` | store the same float32 bytes per backend; the pinned-matrix law survives the seam untouched |
| 7 | subtree law SQL: `class like 'X%'` spelled out, never assumed | `130-139` | `LIKE` works identically; the helper is already shared |
| 8 | file mechanics, not SQL: seal/`.enc` husk, key file, the read-only husk probe (`mode=ro` open, `1708`) | server-level + `1700-1715` | stays a file-level concern; the PG backend needs its own "empty truth" probe (data dir exists, table empty) |

Direct consumers outside the seam: **7 tools open raw `sqlite3`** - `curate_loop.py`,
`m2_merge.py`, `m4_embed.py`, `m4_floor.py`, `m4_shelves.py`, `m4_stuffing.py`,
`test_m2_merge.py` - and the probe fleet (`m4_expose`, `m4_backfill`, `m4_blockrule`, the lme
harnesses) rides `MemoryService`, so it is covered automatically. The P3 importer ships a
backend-aware reader so the 7 raw tools keep working against a PG store; nothing may assume
`sqlite3.connect` against a live store after P3.

## 2. The research: four assumptions checked against 2026 sources

**Vectors - pgvector 0.8+ fixes the filtered-recall trap, but we do not need it yet.** Every
recall query here filters (scope, optionally class), and pgvector's README (github.com/pgvector/
pgvector) documents that approximate indexes under post-filtering return fewer rows than asked
until 0.8's **iterative index scans** are enabled - the AWS Aurora blog (2025-07) measures the
same. At 2,707 live rows (and 11k in venue) the exact scan is the honest design and the math is
identical either way; `vector(384)` is float32-exact while `halfvec` is lossy fp16. Decision:
P2 stores the same float32 bytes and keeps the pinned-numpy read path; the pgvector index is
installed but UNUSED until the 1M-fact trigger in ADR-001 makes an in-server scan necessary.

**Packaging - the P4 risk ADR-001 priced in is largely retired by `pgserver`.** `pgserver` 0.5.0
(PyPI, 2026-06) ships static Postgres **18.3** binaries inside manylinux and Windows wheels,
starts via `get_server(data_dir)`, hands back a connection URI, shuts down when the owning
process exits, is multi-process safe on one data dir, and builds pgvector IN (`load_ext=
["pgvector"]`). The old 0.1.4 line capped at cp312 and would not import on this venv
(python 3.14.8, checked); 0.5.0 declares no Python ceiling. The P4 acceptance test is simply
"the wheel installs and starts on the target PC" - checked at P4, not promised now.

**Driver - psycopg 3.3 `psycopg[binary]`** bundles libpq in one wheel (psycopg.org, 2025-12);
the sync API fits the one-lock threading model unchanged; `psycopg_pool` exists for the
post-flip multi-writer future. Nothing is installed in `.venv` today (checked: no psycopg,
psycopg2, asyncpg, sqlalchemy) - the seam's job is that NOTHING above it notices.

**FTS parity is the landmine, and it is three separate fuses:**
- *Ranking*: `bm25()` (IDF + length normalization) and `ts_rank_cd()` (proximity/TF) genuinely
  differ (Crunchy Data, "Hybrid Search Patterns with Postgres and pgvector", 2025-02). This
  store softens that by accident of design: the lexical arm feeds the blend a **rank position**,
  not the bm25 value (`memory.py:1165`), and RRF then votes `1/(1+rank)`. Ranking divergence
  arrives as a few rank swaps, not score shifts.
- *Stemming and stopwords*: FTS5 `porter` is the Porter stemmer with NO stopword removal; PG's
  `english` config is a snowball stemmer PLUS an English stopword list - divergent on both axes
  (PG docs, Ch. 12.2 and 12.9). PG's predefined **`porter` config** (Porter stemmer, simple
  dict, no stopwords) is the closest match to FTS5 and is what the PG backend must use.
- *Tokenizer*: `unicode61` and PG's default parser split differently - `"user's"` becomes
  `user` + `s` under unicode61 and one word token under PG's parser. A custom text-search
  configuration (default parser + porter dict + empty stopword list) gets closest; the residue
  is MEASURED, not assumed: **P2 acceptance adds an FTS parity battery** - the same corpus and a
  query set through both backends, comparing candidate SETS (coverage is the hard requirement;
  rank swaps fall inside the venue noise-floor budget that gates the flip anyway).

One correction the research forces on ADR-001's text: "the date-bug class dies at INSERT" is
true of the IMPORT/extract layer (typed parameters, no string guessing), not automatic from the
column type alone - the claim lives in P3's importer tests, where it is checkable.

## 3. The shape that falls out: a thin adapter, not a rewrite

Rewriting 58 statements into repository methods is a bigger diff than hiding the seven syntax
divergences behind a dialect shim. So P1 builds:

- a `Store` seam with the SAME surface the call sites already use (`execute`, `executescript`,
  `commit`, `lastrowid`, `close`) - the existing 431 tests become the parity gate for free;
- a small dialect layer: identity/DDL text, `insert or ignore` → `ON CONFLICT DO NOTHING`,
  `pragma table_info` → `information_schema`, the FTS match-builder and its order-by clause,
  the empty-truth probe, and the lock (kept on both backends until after the flip);
- P1 lands in two commits: **(i)** the seam, SQLite-only - zero behavior change, all 431 green,
  and the live store file byte-identical before/after (hash the copy); **no reboot needed** for
  commit (i) alone. **(ii)** Postgres as backend B and the doubled test matrix, per ADR-001.

P2 (the venue-level parity gate) then follows ADR-001 as written, with two amendments this loop
produces: the FTS parity battery joins P2's acceptance, and P4's packaged-subprocess risk shrinks
to a wheel check on the target PC (the pgserver finding). The open owner decision, flagged early
on purpose: **at-rest encryption**.
The seal/`.enc`/key-file law is file-level and does not map onto a PG data directory of many
files; at P5 the choice is (a) ship PG with data-dir permissions + encrypted `pg_dump` backups
and an honest banner, or (b) reimplement ciphertext at rest. Nobody should discover this at flip
time.

What the seam does NOT touch: the recall math (RRF_K=1.0, blend weights, the pinned-matrix
cosine), the curation loop and merge law, the `/memory` API, the block/pin-cache laws, and every
default - SQLite stays the shipped default until P5 says otherwise with numbers.
