# ADR-001: The store engine - SQLite through beta-1, PostgreSQL as the 1.x migration

**SUPERSEDED the same day by [ADR-002](0002-postgres-native-rebase.md)** - the user's call:
PostgreSQL becomes the base with a full native rebase, no shim, no dual-backend phasing. The
audit and research below stand (they fed ADR-002); the P1-P5 plan, the shim shape, and the
escape hatch do not. Historical record, kept for the reasoning trail.

**Was: accepted (2026-10-16, the user's decision; phasing agreed the same day).**
**Deciders: the user (direction, ownership of claims); Clio (measurements and the phased plan).**
**Build spec: [docs/POSTGRES_BUILD.md](../POSTGRES_BUILD.md) - the code audit + research loop run
before P1 (2026-10-16): the storage-surface inventory, the pgserver/pgvector/psycopg/FTS-parity
findings, the thin-adapter seam shape, and the two amendments this loop produces below.**

## Context

Mnemosyne runs on SQLite: one file, zero config, one server process holding one connection under one
lock. The user weighed three published comparisons (dev.to, DataCamp, Airbyte) that all pick
PostgreSQL for production - and all three, in the same breath, pick SQLite for "embedded systems,
desktop tools, edge computing, zero-configuration deployment", which is Strata's exact product shape.
So the argument could not be settled by articles; it was settled with our own measurements:

- recall latency 62 ms median (11k-row venue store), of which the SQL filter is **0.05 ms** - the DB
  is ~0.1% of the recall path; the embedder and the cosine matrix are the other 99.9%;
- writes are embedder-bound (5.5 rows/s at 10k rows, the quadratic matrix rebuild fixed separately);
- one user, one machine, one process, `127.0.0.1` - the MVCC scenario cannot occur through the API;
- the entire probe fleet (venue copies, the 3-second replay guard, every `--offline` instrument)
  is built on copying a file, and backups are `cp`.

But the long-term case for PostgreSQL is real, and two of its arguments are things this project has
personally bled over:

- **typed time**: the 2026-10-15 slash/hyphen date bug (`haystack_dates` arrive as `2023/04/10`,
  the parser wanted `-`, and `day_of()` returned "" for every date since inception - silently
  degrading three measurements) is a `TEXT` column doing dates by convention. `TIMESTAMPTZ` makes
  that bug class impossible at insert;
- **pgvector + HNSW** is the maturing ANN path when the store outgrows brute-force cosine (~1M
  facts; at today's 2.6k the pinned numpy matrix is honestly *faster* than any cross-process query);
- **JSONB + GIN** turns the provenance backlog (subjects table, document spine, graduation tests)
  into native queries instead of scans;
- **MVCC** matters the day a second writer is real: a separate curation daemon, or the networked
  vision - one Mnemosyne serving several machines, several Strata instances, one memory;
- **PITR and basebackup** replace `cp` when the store becomes the thing you cannot lose;
- **template-DB copies** (`CREATE DATABASE venue TEMPLATE store`) replace the probe fleet's `cp` -
  the pattern survives, the tooling changes.

The 64-to-128 GB RAM upgrade (2026-10-16) removes the resource objection: a managed Postgres at
60-100 MB of shared_buffers is free on that box, where it was a real squeeze at 62 GB with 102 GB
of model streaming.

## Decision

1. **beta-1 ships on SQLite.** The open build list - the auto-shelver (learned shelves + payload
   cutters), the block-level cap-not-quota rule, and everyday-use polish - lands first. They are
   engine-agnostic (tables and classifiers) and they port free.
2. **PostgreSQL is the flagship 1.x project**, phased:
   - **P1 seam**: `MemoryService` already owns every line of SQL; formalize the storage interface,
     keep SQLite as backend A, add Postgres as backend B, run the whole memory test suite against
     **both** (the doubled matrix is the price of the byte-identical rule, paid willingly);
   - **P2 parity gate**: venue reruns, grounding replays, and m4_stuffing on both backends must
     reproduce the filed numbers within the measured noise floor (±0.05 on 127 lines - the floor
     this project calibrated the hard way); *(amendment, 2026-10-16 audit: P2 acceptance also
     includes an FTS parity battery - same corpus, same query set, both backends, candidate SETS
     compared; the porter/unicode61-vs-PG-parser split on apostrophes is a measured risk, see
     docs/POSTGRES_BUILD.md §2);*
   - **P3 importer**: SQLite -> Postgres one-shot (TEXT->TIMESTAMPTZ, INTEGER PK->IDENTITY,
     FTS5->tsvector/GIN, pinned vectors->pgvector(384)+HNSW, provenance->JSONB); the data is
     megabytes, the code is the ~20 service methods plus packaging, estimated 2-3 focused weeks;
     *(amendment, 2026-10-16 audit: vectors move as float32 `vector(384)` bytes and the pinned-
     numpy read path is UNCHANGED - the HNSW index is installed but unused until the 1M-fact
     trigger needs it, and `halfvec` stays out (lossy fp16 breaks score parity); the typed-
     parameter win lives in the importer's own tests, not automatically in the column type);*
   - **P4 packaging**: "starts with Strata" - a bundled portable Postgres as a supervised
     subprocess (initdb on first run, start/stop with the server, scram secret beside the existing
     key-file, `127.0.0.1` only, banner line like every other activation); *(amendment,
     2026-06 find, 2026-10-16 audit: `pgserver` 0.5.0 ships Postgres 18.3 + pgvector inside
     manylinux/Windows wheels with auto-shutdown and multi-process safety - P4's risk shrinks to
     "the wheel installs and starts on the target PC", verified at P4, not promised now);*
     *(R0 correction, same day: pgserver 0.5.0 was a search-snippet artifact - real latest is
     0.1.4/cp312-capped; the measured wheel is `embedded-postgres` 18.6.3, PG 18.6 + pgvector,
     proven on this box: start 0.3 s, createdb 3 ms. See ADR-002. R0b named the culprit the same
     hour: the snippet was describing `pixeltable-pgserver`, Pixeltable's maintained pgserver
     fork - which is also the Windows answer.)*
   - **P5 flip**: default becomes Postgres only when P2 passes; `store.engine: sqlite` remains as
     the escape hatch.
3. **Revisit triggers** (any one accelerates P1): the DB exceeds 10% of measured recall latency at
   real size (today ~0.1%); the store passes ~1M facts and cosine exceeds budget (try sqlite-vec
   first); genuine multi-host or second-process direct access appears; a write wall that is not the
   embedder.

## Consequences

**Gained**: a schema where the date bug class cannot exist; ANN headroom to millions of facts;
provenance as a queryable citizen; real concurrency when the product grows into it; point-in-time
recovery; probe clones via templates.

**Paid**: a doubled test matrix; ~50 MB of bundled binaries plus daemon lifecycle (crash recovery,
upgrade policy) in the installer; probe tooling rewritten from file copies to dump/template;
2-3 focused weeks that do not ship user-visible features; and the honesty that at today's size
Postgres is **not faster** - it is *later-capable*. The default-path rule survives every phase:
with the config key absent, the shipped path stays byte-identical to the release before it.

**Falsifiable**: if P2 parity cannot reproduce the filed venue numbers within the noise floor,
the flip does not happen - the SQLite path stands and this ADR is reopened with that evidence.
