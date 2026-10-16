# ADR-002: PostgreSQL is the base - a full native rebase, superseding the phased bootstrap

**Status: accepted (2026-10-16, the user's decision, replacing the same-day phasing of ADR-001).**
**Deciders: the user (direction: "make Postgres the base and replace SQLite fully, no shim, make
them native... do the hard work of completely rebasing"); Clio (the R0 measurements below).**
**Supersedes: ADR-001** (SQLite-through-beta-1 with a P1-P5 dual-backend migration). ADR-001's
audit and research stay valuable and are folded in; its phasing, shim, and escape hatch do not.

## Decision

PostgreSQL becomes the store engine, period. `MemoryService` talks **native Postgres** -
psycopg 3, native DDL, native types, native text search - with no SQLite backend, no dialect
shim, and no dual-backend test matrix. beta-1 ships on Postgres. The one-way importer exists to
carry existing stores (the dev box's 2,707-fact store and the eval fixtures) across, not to keep
SQLite alive as a runtime option.

Why the rebase beats the bootstrap - three facts the audit produced:

1. **There is no installed base to protect.** The memory fork has shipped to nobody; the stores
   that must survive the move are one dev box and rebuildable eval fixtures. ADR-001's phasing
   existed to de-risk migrating *users*; there are none yet, so the phasing tax (two dialects
   forever, a doubled matrix, a parity gate between backends) buys nothing.
2. **The shim would be the hard part anyway.** The audit (`docs/POSTGRES_BUILD.md` §1) inventoried
   58 statements / 63 call sites and eight groups of SQLite coupling. Writing the dialect shim is
   most of the work of just *not* rebasing; writing native once replaces it and retires the
   doubled matrix.
3. **The runtime bet measured green in R0** (below), on this box, same day: the server starts in
   0.3 s over a unix socket, search answers in 1.6 ms at real store size, and packaging is a
   pip wheel.

## R0: the measurements (2026-10-16, this machine: RTX 4090, python 3.14.8, /tmp cluster)

| what | number | note |
|---|---|---|
| `embedded_postgres` 18.6.3 initdb + start | **0.3 s** | first boot of a fresh data dir; stop 0.1 s |
| Postgres version in the wheel | **18.6** | `py3-none-manylinux` wheels - no Python-version ceiling (works on 3.14) |
| extensions available | **vector, pg_trgm, unaccent, hstore** | pgvector IS bundled; unused until the 1M trigger |
| `createdb` (the per-test-DB primitive) | **3 ms median** | a fresh database per test is free; 431 tests need no shared-state gymnastics |
| insert 2,707 rows + generated tsvector + GIN | **0.03 s** | the whole live store loads in a blink |
| tsvector GIN + `ts_rank_cd` top-60, 2,707 rows | **1.6 ms/query** | vs ~0.1 ms in-process bm25; +2.4% of the 62 ms embed, noise |
| insert 2,707 float32(384) blobs | **0.04 s** | vectors move as `bytea`, the pinned-numpy read path unchanged |
| full vector-table read | **5.2 ms** | once per store change, same pin law as today |
| psycopg | **3.3.6** `psycopg[binary]` | libpq bundled in the wheel, sync API fits the one-writer model |

**Research correction, filed same day (a wrong number is corrected publicly):** ADR-001's P4
amendment and `docs/POSTGRES_BUILD.md` §2 cited **pgserver 0.5.0 with PG 18.3** - pgserver's real
latest is **0.1.4** (June 2024, cp310-cp312 wheels, dead on python 3.14). The packaging that
actually works here is **`embedded-postgres` 18.6.3**: PG 18.6 binaries as a pip wheel with
initdb/start/stop handled, unix-socket URI, multi-process safe via fasteners. `pgserver-extend`
(0.0.2, cp310-cp313) also misses 3.14.

**R0b - the user's leads checked the same hour (pixeltable + postgresql.org):** the stale
"pgserver 0.5.0 / PG 18.3" snippet now has a NAME - it was describing **`pixeltable-pgserver`**,
Pixeltable's actively maintained fork of pgserver (PyPI 0.6.0), which is exactly where that
version/PG pair lives. Measured on this box with the identical battery: initdb+start **0.3 s**,
stop **0.1 s**, createdb **4 ms** median, pgvector bundled - but its wheel carries **PG 18.4**
(embedded-postgres carries 18.6) and cp310-cp314 C-extension tags (embedded-postgres is
`py3-none`, immune to Python-minor churn). The decisive difference runs the other direction:
**pixeltable-pgserver ships `win_amd64` AND `win_arm64` wheels** (cp310-cp314, in both 0.5.1 and
0.6.0, checked from the release files) where embedded-postgres ships none - and it carries
`upgrade_db`/`pgdata_version` helpers plus a `postgres_version=18` parameter. So the packaging
answer is not one winner but a **per-OS wheel choice behind one `get_server(data_dir) -> URI`
call** (an OS branch, not the SQLite/PG dialect shim the rebase retired): Linux/macOS =
`embedded-postgres` (newer stable PG, more extensions bundled: vector + pg_trgm + unaccent +
hstore); Windows = `pixeltable-pgserver` (the only win wheels that exist, pgvector included).
Pixeltable itself (0.7.17, "the unified multimodal backend") runs production on the
embedded-pgserver pattern - the shape this rebase bets on is proven by someone else at scale.

**PG version policy (from postgresql.org, 2026-10-16):** stable is **18.6** (what R0 measured);
**PostgreSQL 19 RC 1 shipped the day before the audit** (2026-10-15), final expected ~November.
We build on 18 stable - the schema uses only core-18 features the feature matrix confirms (GIN,
stored generated columns, upsert, `RETURNING`; UUIDv7 exists in 18 but bigint IDENTITY is kept
for id-preservation across the import; AIO in 18 is a free win). PG 19 is a **wheel swap +
`pg_upgrade`**, not a rebase event: both packaging tracks follow PG majors (embedded-postgres
versions its wheels by PG release; pixeltable-pgserver takes `postgres_version` as a parameter),
and no RC is chased before it is final. The gap named early is now CLOSED: 18.6.3's missing
Windows wheel is no longer a beta-2 question mark - the Windows answer is named, versioned, and
pgvector-bundled.

## The native shapes that replace the SQLite-isms

- **Text search**: PG 18 has **no `porter` config and no porter dictionary** (measured - they are
  gone). The native answer is a custom configuration, created by the schema and used by a
  `tsvector` GENERATED column + GIN:
  `strata_fts` = default parser + `english_stem` (snowball) + **no stopword list**, with the
  unicode61 apostrophe law reproduced natively:
  `to_tsvector('strata_fts', translate(lower(text), '''', ' '))`.
  Measured behavior vs FTS5: `"user's"` indexes as `user` and a `user` query matches (True);
  hyphenated words index the whole token AND the parts - a **superset** of FTS5's splits, so
  recall gains matches, never loses them; snowball's built-in stop list drops ultra-common tokens
  (`don`, `t`, `the`, `s` measured gone) - harmless because `to_tsquery` applies the same law to
  the query side. The residue - snowball-vs-porter stemming divergence on individual words - is
  exactly what the venue re-record below gates; it is measured, not assumed.
- **Identity**: `bigint GENERATED ALWAYS AS IDENTITY`; inserts take `RETURNING id` (no `lastrowid`).
- **Time**: `timestamptz` native; the API keeps its epoch-float contract (conversion at the
  service boundary), so `day_of` and every caller keep working.
- **Provenance and the exposure ledger**: `provenance jsonb`, `injected jsonb` - native, queryable.
- **Vectors**: `bytea` float32 + the pinned-numpy exact scan, unchanged (it is the measured
  design); `CREATE EXTENSION vector` is the documented step at the 1M trigger, and it is bundled.
- **Upserts**: `ON CONFLICT DO NOTHING`; migrations via `information_schema.columns`.
- **At rest**: the seal/`.enc`/husk law is file mechanics over one file and does not map to a
  data directory. The native replacement: data dir `0700`, and **encrypted `pg_dump` backups**
  (AES under the existing key-file beside the store) - the key-file law survives, the husk rules
  retire. `127.0.0.1`-never-exposed is satisfied even better: the socket never leaves the data dir.
- **One-writer law**: kept. One connection, one lock, now with real transactions (rollback free);
  MVCC multi-writer is a measured upgrade for later, not a day-one change.

## What dies, what survives

Dies: the dialect shim and dual-backend matrix (ADR-001 P1-P2), the parity gate between backends,
the `store.engine` escape hatch, `insert or ignore`, `pragma table_info`, the `.enc` seal/shred/
husk rules, and raw `sqlite3` in the seven tools (they rewrite native - they are ours).
Survives untouched: every line of recall MATH (RRF_K=1.0, the blend weights, the rank-position
law, the pinned-matrix cosine), the curation loop and merge law, the auto-shelver, the block and
pin-cache laws, the `/memory` API, and the measurement protocol.

## Phases (R = rebase)

- **R1 schema**: native DDL as one `executescript`-equivalent - tables, `strata_fts` config,
  generated tsvector + GIN, identity columns, jsonb, `information_schema`-based migrations.
- **R2 the rewrite**: `MemoryService` on psycopg 3 - the audit's 58-statement inventory is the
  work checklist; behavior stays identical everywhere the SQL is not the point.
- **R3 test harness**: one shared cluster per suite run (0.3 s), a fresh database per test
  (3 ms); all 431 green on Postgres, no SQLite left in the suite.
- **R4 importer**: one-way, ids preserved, vectors byte-copied, FTS rebuilt by insert; runs on a
  copy of the live store first, counts and spot-recall verified before anything cutover-touches.
- **R5 tools**: the seven raw-sqlite tools + venue rebuild + eval stores, native.
- **R6 packaging**: `setup.py` installs `psycopg[binary]` plus the **per-OS server wheel** -
  `embedded-postgres` on Linux/macOS, `pixeltable-pgserver` on Windows (one `get_server(data_dir)
  -> URI` call either way); the server starts the cluster beside the store dir, banner line like
  every other activation, encrypted backups per above.
- **R7 cutover**: import the live store, dev server runs on Postgres - **this is the one reboot**
  - then venue + eval stores rebuild on PG and the numbers re-record.

**The regression gate, with its falsifier stated before the run:** the venue replay re-records on
Postgres. Retrieval-level metrics are deterministic, so gold coverage must MATCH the SQLite
baseline or the difference must be explained row-by-row; answer-level metrics carry the ±0.05
noise floor. If coverage drops more than the floor, the fix is the tokenizer CONFIG (mapping,
normalization) - not the engine - and it is tuned before R7 is accepted. If no config recovers
parity, that is the falsifier that this rebase stops and re-plan: the decision would then flip
back to ADR-001's phasing with the shim, and we will know precisely which rows diverged and why.
