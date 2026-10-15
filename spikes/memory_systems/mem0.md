# mem0 (mem0ai/mem0) — deep read for Strata tier-3

Researched 2026-10-14 against `main` @ `0724291bd864cf807e88ee8981c8226644e8fb42`
("Add OSS Halloween Happy Hour banner to README (#7637)", committed 2026-10-14),
shallow-cloned to `/tmp/opencode/research/mem0` (57 MB; `mem0/` Python package
≈ 6,093 LOC across the four files that matter: `memory/main.py` 3,912,
`configs/prompts.py` 1,062, `utils/entity_extraction.py` 772, `memory/storage.py` 347).
Package version `2.2.1` (`pyproject.toml:7`), license **Apache-2.0**
(`pyproject.toml:13`, `LICENSE`).
Repo: https://github.com/mem0ai/mem0 · paper (2025): https://arxiv.org/abs/2504.19413 ·
docs (vendored in-repo under `docs/`, published at https://docs.mem0.ai) ·
eval harness: https://github.com/mem0ai/memory-benchmarks

**The single most important thing to know before reading the rest:** mem0 is now
*two different systems*. The v3/"token-efficient" algorithm shipped April 2026 replaced
the paper's extract→ADD/UPDATE/DELETE/NOOP design with **single-pass ADD-only
extraction**, and moved consolidation, decay, temporal ranking and graph memory to the
**closed managed Platform**. The open-source SDK we cloned has *no* conflict resolution,
*no* recency term, *no* supersede, and *no* graph. All file paths and line numbers below
are from the clone and are exact; quoted line numbers inside issue text are from other
commits and are marked as such.

---

## What it is

- **A memory *layer* between an app and a model, not a store.** Two products share one
  API: managed Platform (`MemoryClient`) and self-hosted OSS (`Memory`/`AsyncMemory`).
  You call `add()` after a turn and `search()` before the next model call; the app decides
  what goes into the prompt (`docs/core-concepts/how-it-works.mdx`). OSS Python defaults:
  LLM `gpt-5-mini` (`mem0/llms/openai.py:39-40`), embedder OpenAI
  `text-embedding-3-small` (`mem0/embeddings/configs.py:6-8`), vector store **Qdrant**
  (`mem0/vector_stores/configs.py:8-9`), history in SQLite at `~/.mem0/history.db`
  (`mem0/configs/base.py:41-43`).
- **Three stores, not one.** SQL (facts + metadata + history log + a rolling 10-message
  window), a vector DB (embeddings), and a **parallel entity collection**
  `{collection}_entities` (`mem0/memory/main.py:559-581`,
  `docs/core-concepts/how-it-works.mdx` "Where memories live"). The self-hosted server
  swaps Qdrant for Postgres+pgvector (`server/main.py:123-136`) but keeps the same
  three-store shape. Nothing is co-located in one file/db; **no encryption at rest exists
  anywhere in the Python package** (negative grep for `encrypt|cipher|sqlcipher` over
  `mem0/`).
- **Write = one LLM call, ADD only.** `_add_to_vector_store` (`mem0/memory/main.py:881`)
  runs an eight-phase batch pipeline (comment `=== V3 PHASED BATCH PIPELINE ===` at
  `:918`): fetch last 10 messages → vector-search top-10 existing memories → **one**
  `generate_response` with `response_format={"type":"json_object"}` (`:942-973`) →
  batch-embed → **MD5 hash dedup only** (`:1022`) → batch insert → entity linking → save
  messages. `add()` returns `event: "ADD"` and nothing else (`:1209`, and the migration
  table `docs/migration/oss-v2-to-v3.mdx`: "add() events | Returns ADD, UPDATE, DELETE |
  **Returns ADD only**").
- **Read = three signals fused additively, LLM-free by default.** `_search_vector_store`
  (`:1642`) runs semantic search over an over-fetched pool (`internal_limit =
  max(limit*4, 60)`, `:1655`), a BM25 `keyword_search`, and an entity-boost lookup, then
  `score_and_rank` (`mem0/utils/scoring.py:60`) combines
  `semantic + bm25 + entity_boost` divided by an adaptive max. `rerank=False` and
  `self.reranker` is `None` unless configured (`:1399`, `:506-511`), so **the default read
  path spends zero LLM calls**.
- **BM25 is a *rank* signal, never a recall expander.** Candidates come only from the
  semantic list; keyword hits that the vector search missed can never enter the result set
  — their own migration doc states it: "**BM25 is a boost signal, not a recall expander.**
  Only semantic search results are candidates" (`docs/migration/oss-v2-to-v3.mdx`), and
  the code confirms it: `score_and_rank` iterates `semantic_results` and `continue`s on
  `semantic_score < threshold` *before* adding the BM25 term
  (`mem0/utils/scoring.py:111-118`).
- **Graph memory is gone from OSS.** `enable_graph` + `graph_store` + all Neo4j/Memgraph/
  Kuzu/AGE/Neptune drivers were deleted — "~4000 lines" — and graph memory became an
  always-on Platform feature (`docs/migration/oss-v2-to-v3.mdx` "Graph Memory: Platform
  Only"; `docs/core-concepts/how-it-works.mdx`: OSS "boosts on entity overlap alone: **it
  has no graph memory**"). The 2025 paper's `Mem0ᵍ` (typed triplets in Neo4j,
  arXiv §2.2) no longer exists in this codebase.
- **Consolidation exists — as a paid cloud feature.** Platform's **Dream** does Merge +
  Supersede + Synthesis, stores `lifecycle_state` / `replacement_memory_id` as *indexed
  columns*, and reads are filtered with `latest_only=true` / `include_merged=true`
  (`docs/platform/features/dream.mdx`; https://mem0.ai/blog/dream-background-memory-consolidation-for-ai-agents).
  OSS has none of it: `grep -rn "is_active|supersed|recency|last_accessed|access_count" mem0/`
  returns only client-SDK docstrings about the Platform (`mem0/client/main.py:530-532`).
- **Operational surface is enormous and polished**: 28 vector-store backends
  (`mem0/vector_stores/*.py`), 16 of which implement `keyword_search`
  (`grep -l "def keyword_search" mem0/vector_stores/*.py`), 18 LLM providers, 11 embedders,
  5 rerankers, a self-hosted FastAPI server + dashboard with auth (`server/README.md`),
  an MCP server, a CLI, 21 documented framework integrations, and 18 packages under
  `integrations/` (Claude Code, Cursor, Codex, OpenClaw, n8n, Zapier, Vercel AI SDK…).
- **Telemetry is on by default** and leaves the box: `MEM0_TELEMETRY =
  os.environ.get("MEM0_TELEMETRY", "True")` → PostHog at `https://us.i.posthog.com`
  (`mem0/memory/telemetry.py:14,16`), capturing init/add/search/get_all/delete events
  (`mem0/memory/main.py:1214-1219` etc.).

### Portability verdict at a glance

| Mechanism | LLM per **write** | LLM per **read** | Extra infra | Fits Strata's one-GPU box? |
| --- | --- | --- | --- | --- |
| ADD-only extraction (`main.py:942`) | **yes, 1 call, ~8.4k-token system prompt** | – | LLM provider | Only off the hot path, and only with a much shorter prompt |
| `infer=False` raw store (`main.py:882-916`) | no | – | – | yes (that is our journaled-turn ingest) |
| MD5 hash dedup (`main.py:1022`) | no | – | – | yes, we already do exact-text dedup |
| Entity store + boost (`main.py:1747`, `scoring.py`) | no (spaCy NER + rules) | no | spaCy `en_core_web_sm` + a second vector collection | **yes, as an inverted index — the best idea in the repo** |
| BM25 keyword arm (`qdrant.py:454`, `lemmatization.py`) | no | no | fastembed sparse vectors / store FTS | yes (we already have FTS5 bm25) |
| Additive fusion, semantic-gated (`scoring.py:60`) | no | no | – | partially — the *normalisation* is portable, the *semantic gate* is not |
| Rerankers (`mem0/reranker/*`) | no | **optional** (Cohere/ZE API, or local cross-encoder) | model weights | yes — `cross-encoder/ms-marco-MiniLM-L-6-v2` (`sentence_transformer_reranker.py:41`) or `BAAI/bge-reranker-base` (`huggingface_reranker.py:40`) |
| Graph memory / `Mem0ᵍ` | yes (2 LLM passes in the paper) | no | **graph DB** (was Neo4j) | no — and they deleted it themselves |
| Dream (merge/supersede/synth) | yes, background | no | closed service | conceptually yes (that is our curation loop); code no |
| Memory Decay (0.3×–1.5× on access recency) | no | no | closed service | yes, the idea is 10 lines for us |
| Temporal Reasoning (write-time date metadata) | yes, separate pass | no | closed service | partially |
| Custom categories (LLM tags) | yes (same call) | no | closed service | we already do this locally with `class` |

---

## Write path

`Memory.add()` (`mem0/memory/main.py:760`) → `_add_to_vector_store()` (`:881`). The
in-code phase comments are exact anchors:

```
:918  # === V3 PHASED BATCH PIPELINE ===
:920  Phase 0: last 10 messages of this session scope   (SQLite `messages` table)
:925  Phase 1: vector search top_k=10 in {user,agent,run} scope  → "Existing Memories"
:935  existing ids remapped to "0","1",… (anti-hallucination), uuid_mapping kept
:942  Phase 2: ONE llm.generate_response(ADDITIVE_EXTRACTION_PROMPT, user_prompt,
              response_format={"type":"json_object"})
:975  parse → extracted_memories = json["memory"]
:993  Phase 3: embed_batch(all extracted texts)
:1007 Phase 4/5: per-row CPU work + MD5 hash dedup vs existing + vs batch
:1047 Phase 6: batch insert → batch history rows (event ADD)
:1100 Phase 7: extract_entities_batch → embed → search_batch → upsert entity rows
:1206 Phase 8: save_messages (rolling 10) → return [{"id","memory","event":"ADD"}]
```

**The prompt is the product.** `ADDITIVE_EXTRACTION_PROMPT`
(`mem0/configs/prompts.py:468-946`) is **33,658 characters ≈ 8,414 tokens** (measured on
the clone) of static instruction: what to extract (user *and* assistant messages, "when in
doubt, extract"), **8 quality standards** (`## Memory Quality Standards`,
`prompts.py:605+`: contextually-rich-not-atomic, clean factual statements, self-contained,
concise-but-complete 15–80 words, temporally grounded, numerically precise, never generalize
concrete information, meaning-preserving), **7 integrity rules** (`:677+`: no fabrication, no
implicit attribute inference, correct attribution, **no echo extraction**, no within-response
duplication, no meta-extraction, no detail contamination from context), and **11 worked
examples** numbered 1–12 (their numbering skips "Example 4"), including a D&D stat-block and
a multi-speaker conversation. An "Exhaustive Extraction
Checklist" closes it: *"for conversations with 10+ messages, you should typically extract
5-15 memories"*. Output schema (`:918-941`): `{"memory":[{"id","text","attributed_to",
"linked_memory_ids"?}]}`.

Independent corroboration of the write cost: effloow measured "the same **33,653-character
extraction prompt, estimated at roughly 8,478 tokens**, on every turn" and
**1.52 s per ingested turn** with search at **38 ms p50** (Mem0 2.1.0, local Qdrant) —
https://effloow.com/articles/mem0-zep-letta-agent-memory-latency-benchmark-2026. That is
within 5 characters of our own measurement of the constant.

Two context sources exist in the prompt builder but are **empty in OSS**: `Summary`
(a narrative user-profile summary) and `Recently Extracted Memories` —
`generate_additive_extraction_prompt` is called with only
`existing_memories`, `new_messages`, `last_k_messages`, `custom_instructions`
(`mem0/memory/main.py:950-955`; signature `mem0/configs/prompts.py:1016-1025`). The
summary input is Platform-only (`get_summary`, `docs/platform/platform-vs-oss.mdx`).

**Temporal grounding is done in the prompt, not by code.** "Observation Date" (when the
conversation happened) and "Current Date" are separate inputs, and the prompt forbids
resolving relative references against the wrong one: *"'User went to Paris last week' is
useless 6 months later. 'User went to Paris the week of May 15, 2023' is meaningful
forever"* (`mem0/configs/prompts.py:524-535`). This rule exists because the platform got it
wrong: issue #3944 reproduced LoCoMo at **LLM score ≈ 0.20** on the managed platform and
traced it to *"the system is using the current date/time (e.g., January 2026) instead of
the timestamps provided"* (https://github.com/mem0ai/mem0/issues/3944).

**Nothing in the write path decides anything.** The old update-phase prompt
`DEFAULT_UPDATE_MEMORY_PROMPT` (`mem0/configs/prompts.py:176`, with its
`ADD/UPDATE/DELETE/NONE` tool-call contract, `:185`) and its builder
`get_update_memory_messages` (`:406`) are **dead code in OSS** — grep shows no call site
outside `prompts.py` itself. The only automatic conflict handling left is:

```python
# mem0/memory/main.py:1021-1025
mem_hash = hashlib.md5(text.encode()).hexdigest()
if mem_hash in existing_hashes or mem_hash in seen_hashes:
    logger.debug(f"Skipping duplicate memory (hash match): {text[:50]}")
    continue
```

Their own docs state the consequence plainly: *"The automatic extraction path is additive.
If a user says 'I moved from Austin to Seattle,' Mem0 can store the new fact without
silently rewriting the old one. Use explicit `update` or `delete` operations when your
application needs to correct or remove a memory"*
(`docs/core-concepts/how-it-works.mdx`).

**The LLM's `linked_memory_ids` is silently dropped.** The prompt spends a whole section
teaching memory→memory linking and promises *"this enables the system to build a graph of
related memories"* (`mem0/configs/prompts.py:692-698`, example at `:843-852`). In the add
pipeline only `attributed_to` is persisted from the model's output
(`mem0/memory/main.py:1037-1038`); `linked_memory_ids` appears in `main.py` **only** as
the *entity* row's `linked_memory_ids` list (entity → memory ids, `:624-641`, `:1171-1189`).
TypeScript is the same: the zod schema parses the field
(`mem0-ts/src/oss/src/prompts/index.ts:781`) but the payload builder stores only
`attributedTo` (`mem0-ts/src/oss/src/memory/index.ts:1038-1039`). So in OSS today the
"graph of related memories" the prompt asks for does not exist on disk.

**Message-level noise filtering exists but is thin** — `filtering.ts` /
`isNoiseMessage()` landed as PR #4302 catching heartbeats, cron output and generic acks,
and their own audit says it does not touch the dominant junk classes (issue #4573, below).

---

## Read path

`Memory.search(query, top_k=20, filters=…, threshold=0.1, rerank=False, explain=False,
show_expired=False)` (`mem0/memory/main.py:1393`). `filters` **must** contain at least one
of `user_id`/`agent_id`/`run_id` (`:1471`); top-level entity kwargs now raise
(`docs/migration/oss-v2-to-v3.mdx`).

```python
# mem0/memory/main.py:1651-1699  (_search_vector_store)
query_lemmatized = lemmatize_for_bm25(query)          # spaCy lemmas, -ing forms kept too
query_entities   = extract_entities(query)            # PROPER / QUOTED / TOPIC / IDENTIFIER
embeddings       = self.embedding_model.embed(query, "search")
internal_limit   = max(limit * 4, 60)                 # over-fetch for the scoring pool
semantic_results = self.vector_store.search(..., top_k=internal_limit, filters=filters)
keyword_results  = self.vector_store.keyword_search(query_lemmatized, internal_limit, filters)
...
scored_results   = score_and_rank(candidates, bm25_scores, entity_boosts,
                                  threshold, top_k, explain)
```

Fusion (`mem0/utils/scoring.py`):

```python
combined = min((semantic_score + bm25_score + entity_boost) / max_possible, 1.0)
# max_possible = 1.0 | 2.0 | 2.5 | 1.5 depending on which signals are live
if semantic_score < threshold: continue        # gate BEFORE combining
```

BM25 raw scores are squashed to [0,1] by a **logistic sigmoid whose midpoint and
steepness depend on the number of query terms** — `(5.0, 0.7)` for ≤3 terms up to
`(12.0, 0.5)` for >15 (`scoring.py:get_bm25_params`, tested in
`tests/utils/test_scoring.py:12-31`). That is a genuinely clever answer to the
"mixed score scales" problem we hit ourselves (`serve/memory.py:1030` comment).

Entity boost (`main.py:1747-1827`): up to 8 deduplicated query entities, each embedded,
each searched against the entity collection (`top_k=500`, `:1789`), matches below cosine
0.5 discarded, and the boost is **damped by how many memories the entity already points
at**:

```python
# mem0/memory/main.py:1816-1817
memory_count_weight = 1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))
boost = similarity * ENTITY_BOOST_WEIGHT * memory_count_weight   # ENTITY_BOOST_WEIGHT = 0.5
```

`explain=True` returns every component (`score_details`: semantic, bm25, entity,
raw, max_possible, threshold) — `scoring.py:127-133`.

**What is absent, and provably so:** no recency term, no confidence term, no access
history, no per-row state, no query→shelf routing. A production user on 2.0.10 verified
this against upstream: *"`score_and_rank` … orders purely by `semantic + bm25 +
entity_boost` with **no temporal term** — and it is byte-identical to upstream `main`"*
(https://github.com/mem0ai/mem0/issues/4956#issuecomment, user `heliamuso`). Recency and
decay live only in Platform: Memory Decay multiplies an access-derived factor bounded
`0.3×–1.5×` into the score, widens the pool to `top_k × 3` (floor 50), sorts on the
unclamped product, then records one "touch" per returned memory capped at the last 20
(`docs/platform/features/memory-decay.mdx`). Reranking, when enabled, runs after fusion
and is best-effort with a silent fallback (`main.py:1514-1520`).

`get_all` is a plain `vector_store.list` with the same 4× over-fetch and no scoring at all
(`main.py:1340-1391`) — the closest OSS analogue to our "inject the whole shelf" mode.

---

## Organization & scoping

- **Scoping is identity, not topic.** `user_id`, `agent_id`, `run_id` (OSS) plus `app_id`
  and org/project hierarchy (Platform only). One id is mandatory on every read and write;
  they compose with implicit AND (`main.py:314` `_build_filters_and_metadata`,
  `docs/platform/platform-vs-oss.mdx`,
  `docs/platform/features/entity-scoped-memory.mdx`). `add()` takes them top-level,
  `search()`/`get_all()` require them inside `filters` — an asymmetry that trips people up
  and is documented as a breaking change in v3.
- **`agent_id`-scoped writes get a different extraction prompt** (`AGENT_CONTEXT_SUFFIX`,
  appended only when `is_agent_scoped` is true and no `user_id` is present,
  `main.py:943-946`) — i.e. the extractor's *behaviour* is scope-aware. Group chat adds a
  `name`/`actor_id`/`role` attribution path (`main.py:897-905`,
  `docs/platform/features/group-chat.mdx`).
- **Topic classification exists but is Platform-only.** Custom categories: a default
  catalog of 15 labels (`personal_details`, `family`, `professional_details`, `sports`,
  …, `docs/platform/features/custom-categories.mdx`), LLM-assigned at ingestion,
  overridable per project or per `add()` call, **not re-applied to existing memories**,
  and filterable through an allowlist (`v2-memory-filters.mdx`). OSS has no automatic
  tagging at all — you put whatever you like in `metadata` and filter it with the full
  operator set (`eq ne gt gte lt lte in nin contains icontains`, plus `AND/OR/NOT` and
  `*`) via `_process_metadata_filters` (`main.py:1538-1641`).
- **The entity collection is the only "topic" structure in OSS**, and it is an
  unqueryable one: their own blog calls it a regression — *"this is no longer a queryable
  graph interface. The `relations` field from prior versions is gone. Entity relationships
  now influence retrieval ranking but cannot be traversed directly"*
  (https://mem0.ai/blog/state-of-ai-agent-memory-2026).
- **Memory types are a label, not a subsystem.** `MemoryType` =
  `semantic_memory | episodic_memory | procedural_memory` (`mem0/configs/enums.py:4-7`),
  but only `procedural_memory` does anything: `_create_procedural_memory` (`main.py:2007`)
  makes one LLM call with `PROCEDURAL_MEMORY_SYSTEM_PROMPT` and stores the resulting
  workflow summary as a single row. Semantic vs episodic is not distinguished anywhere in
  the write path — their own issue thread asks the question and the docs answer "None of
  these three types has its own dedicated parameter in Mem0"
  (https://github.com/mem0ai/mem0/issues/3644, https://mem0.ai/blog/semantic-vs-episodic-vs-procedural-memory-in-ai-agents-a-complete-comparison).
- **Expiration is the one lifecycle feature that *is* in OSS**: `expiration_date`
  (`YYYY-MM-DD`, UTC, inclusive of the day, fail-open on a malformed value) hides a row
  from `search`/`get_all` without deleting it (`main.py:427-458`, `_payload_is_expired`
  at `:442`, used at `:1684` and `:1369`; `docs/platform/features/memory-expiration.mdx`).
- **Scale is a first-class UX concern.** The SDK warns you, in-band, when you cross
  thresholds: **2,000 memories**, `top_k ≥ 50`, or a search slower than **2.0 s**
  (`mem0/memory/notices.py:117-127`). Our dev store is at 2,521 rows — mem0 would have
  popped that notice.

---

## Update, conflict & forgetting

This is where mem0 and Strata diverge most, so it is worth being precise.

**OSS: nothing happens automatically.** `update()` (`main.py:1829`) and `delete()`
(`:1883`) are explicit, id-addressed API calls an application must decide to make.
`_update_memory` (`:2052`) rewrites the payload in place (new `data`, new `hash`, new
`text_lemmatized`, re-embedded vector), writes a history row with `event="UPDATE"` and the
`old_memory` value, and re-links entities (`:2103-2110`). `_delete_memory` (`:2114`)
removes the vector and writes `event="DELETE", is_deleted=1`. `user_id`/`agent_id`/
`run_id`/`actor_id` are immutable after creation (`update()` docstring, `:1849-1851`).

**History is per-row, event-sourced, and separate from the live record**
(`mem0/memory/storage.py:102-126`):

```sql
CREATE TABLE history (
    id TEXT PRIMARY KEY, memory_id TEXT, old_memory TEXT, new_memory TEXT,
    event TEXT, created_at DATETIME, updated_at DATETIME, is_deleted INTEGER,
    actor_id TEXT, role TEXT)
```

`history(memory_id)` is the only read API over it (`main.py:1960`). There is no
`invalid_at`/`superseded_by` equivalent: the *live* row is whatever the vector store holds,
and the old value survives only as a string in a history row. Structurally that is weaker
than our chain (`serve/memory.py:376-379`, `:469-470`), because you cannot query "the
version that was current on date D" or "all rows retired by this write" without replaying
events.

**The raw transcript is deliberately lossy**: `save_messages` evicts everything beyond the
most recent 10 messages per session scope on every write
(`storage.py:279-291`). Strata journals turns durably; mem0 keeps a window.

**Platform: they rebuilt what they deleted, but behind a paywall and in the cloud.**
Dream's three operations (https://mem0.ai/blog/dream-background-memory-consolidation-for-ai-agents,
`docs/platform/features/dream.mdx`):

| Dream op | Rule as documented | Strata analogue |
| --- | --- | --- |
| **Merge** | "requires the new memory to preserve **all** the information in the old one"; the barer row is marked `merged`, hidden by default, retained | our **payload containment** step in `_semantic_dupe` (`serve/memory.py:502-590`) |
| **Supersede** | "requires a clear replacement of the same fact"; older row marked `superseded`, still returned by default, `latest_only=true` to hide | our **rare-token subject veto + strictly-richer** law |
| **Synthesis** | scheduled, off the request path, groups candidates by similarity, "deliberately conservative", writes *new* pattern rows linked to sources | our curation loop's class rollups — we do not yet write pattern rows |

Their stated diagnosis is the same one we wrote down independently: *"the write path has
to be fast, so each write decision sees only the current conversation and a small set of
similar memories … It doesn't see the rest of the store"*, plus a production number —
*"the median active project carries a few hundred memories that duplicate or contradict
other memories in the same project"*. And their stated invariants match ours: *"Anything
ambiguous results in no action"*, *"Nothing is deleted"*, *"Add and search latency do not
change"*, lifecycle states *"stored as an indexed column"*, and rows flagged `immutable` /
`exclude_from_dream` are *"skipped entirely"*. Runs are **weekly per project per eligible
`user_id`** (vs our ~30-min timer) and are **Pro/Enterprise-gated**.

**Forgetting in OSS is a user problem.** No decay, no access counts, no automatic
pruning. Community response: issue #5330 ships a third-party plugin with an Ebbinghaus
formula `min(access_count,255) · 0.5^(days_since_last_access/7)`, deletion below 0.05, and
a 14-day grace period (https://github.com/mem0ai/mem0/issues/5330); a NousResearch plugin
reports "From 90 stored facts … identified 8 groups of similar memories, deleted 8
duplicates, consolidated 6 entries".

**The failure modes are documented, in the open, and still open:**

| Issue | What it establishes |
| --- | --- |
| [#4896](https://github.com/mem0ai/mem0/issues/4896) | "ADD-only architecture doesn't implement conflict resolution for semantically similar memories" — "my name is LGY" then "my name is LGS" → both stored, both returned, docs promise "latest truth wins". Quotes the MD5 block. |
| [#5867](https://github.com/mem0ai/mem0/issues/5867) | Ronaldo → Messi: both facts survive as separate ADDs; retrieval is ambiguous. |
| [#4956](https://github.com/mem0ai/mem0/issues/4956) | Stale employer outranks current one because scoring has no temporal term. Open since 2026-04-24, 20 comments, last touched 2026-10-06, with **four closed community PRs** (#4965 restore ADD/UPDATE/DELETE, #4969 soft-supersede, #5240 cluster-aware retrieval, #7301 bi-temporal invalidation) — none landed. |
| [#6515](https://github.com/mem0ai/mem0/issues/6515) / #6531 (TS) | Hash dedup is a TOCTOU race: the snapshot is taken in Phase 1 and re-checked in Phase 5 *after* the LLM round-trip, so two concurrent `add()`s produce two rows with identical text **and identical hash**. |
| [#4573](https://github.com/mem0ai/mem0/issues/4573) | A 32-day production audit of 10,134 entries: **97.8% junk**; 52.7% of the junk is the boot/system prompt re-extracted every session ("Operator prefers Telegram" appeared 200+ times); 37.6% of survivors were cosine near-duplicates; and upgrading the extractor from `gemma2:2b` to Claude Sonnet 4.6 barely moved the junk rate — *"The extraction prompt is the bottleneck, not the model."* |
| [#3004](https://github.com/mem0ai/mem0/issues/3004) | The paper's MemGPT baseline has no published code. |
| [#3944](https://github.com/mem0ai/mem0/issues/3944) | Platform LoCoMo reproduced at ≈0.20 (vs 0.669) — date handling. |
| [#5235](https://github.com/mem0ai/mem0/issues/5235) | Users want an evaluation toolkit for memory quality/reliability because failures are "hard to debug". |

---

## Evaluation & published numbers

### Vendor-claimed — the 2025 paper (arXiv:2504.19413, ECAI 2025)

LoCoMo, LLM-as-a-Judge (J), mean of 10 runs ±1 sd, GPT-4o-mini as the inference engine,
temperature 0 for baselines (arXiv §3.3):

| System | Overall J | Search p95 | Total p95 | Context tokens |
| --- | --- | --- | --- | --- |
| **Mem0** | **66.88 ± 0.15** | 0.200 s | 1.440 s | 1,764 |
| **Mem0ᵍ** (graph) | 68.44 ± 0.17 | 0.657 s | 2.590 s | 3,616 |
| Zep (as run by Mem0) | 65.99 ± 0.16 | 0.778 s | 2.926 s | 3,911 |
| **Full-context** | **72.90 ± 0.19** | – | 17.117 s | 26,031 |
| LangMem | 58.10 ± 0.21 | 59.82 s | 60.40 s | 127 |
| OpenAI memory | 52.90 ± 0.14 | – | 0.889 s | 4,437 |
| A-Mem | 48.38 ± 0.15 | 1.485 s | 4.374 s | 2,520 |

All figures from https://arxiv.org/html/2504.19413v1 Tables 1 and 2. Two things the paper
does not headline: **full-context beats both Mem0 variants**, and **the graph variant loses
to plain Mem0 on multi-hop** (J 47.19 vs 51.15) and roughly ties on single-hop
(65.71 vs 67.13) — its gains are confined to temporal (58.13) and open-domain (75.71).
The headline claims — "26% relative improvement over OpenAI", "91% lower p95 latency",
">90% token cost saved" — are all measured against *one* baseline (OpenAI memory) and
against *full-context* respectively.

### Vendor-claimed — the April 2026 "token-efficient" algorithm (managed Platform)

From `README.md` "New Memory Algorithm (April 2026)",
`docs/core-concepts/memory-evaluation.mdx`, and
https://github.com/mem0ai/memory-benchmarks README:

| Benchmark | Old → New | Score | Tokens/query | p50 |
| --- | --- | --- | --- | --- |
| LoCoMo | 71.4 → **92.5** (1425/1540) | 92.5 | 6,956 | 0.88 s |
| LongMemEval | 67.8 → **94.4** (472/500) | 94.4 top-200 / **94.8** top-50 | 6,787 | 1.09 s |
| BEAM 1M | – | 64.1 (70.1% pass) | 6,719 | 1.00 s |
| BEAM 10M | – | 48.6 (50.5% pass) | 6,914 | 1.05 s |

LongMemEval by category (top-200): single-session-user 98.6, single-session-assistant 98.2,
single-session-preference 96.7, **knowledge-update 93.6**, temporal-reasoning 97.0,
**multi-session 88.0**. BEAM 10M by category: temporal_reasoning 16.3, event_ordering
20.2, multi_session_reasoning 26.1, contradiction_resolution 32.5.

Setup, in their words: *"Single-pass retrieval (one call, no agentic loops) at a top_200
retrieval budget. **Scores reflect Mem0's managed platform, which includes proprietary
optimizations not available in the open-source SDK**; open-source users should expect
directionally similar gains but not identical numbers"* (`README.md`). Judge = GPT-5 in
the OSS reproduction table; ±1 point CI attributed to judge inconsistency.

**Their own OSS numbers are 3–6 points lower than the Platform numbers** (memory-benchmarks
README, "OSS with Different Extraction Models", same Qwen-600M embedder + Qdrant + GPT-5
answerer/judge): GPT-5 extraction **91.0%**, GPT-OSS-120B 89.8%, Llama 4 Maverick 88.6%,
Gemma 4 31B 88.6%. Knowledge-update under OSS extraction ranges 89.5–94.9%.

**Docs-vs-docs drift to note:** the migration guide claims "+20 on LoCoMo (71.4 → **91.6**)
and +26 on LongMemEval (67.8 → **93.4**)" (`docs/migration/oss-v2-to-v3.mdx`), while the
README and the evaluation page say 92.5 and 94.4. Same product, same month, two sets of
numbers in the same repo.

### Independently reproduced / contested

- **Zep's rebuttal** (2025-05-06, corrected): Mem0 misconfigured Zep (both speakers mapped
  to the user role; timestamps appended to message bodies instead of `created_at`; searches
  run sequentially), and on LoCoMo *"a simple full-context baseline … achieved a J score of
  ~73%, compared to Mem0's best score of ~68%"*. Zep's corrected score **75.14 ± 0.17**
  (10 runs), ~10% above Mem0's best.
  https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/
- **Mem0's counter-rebuttal:** Mem0's CTO opened getzep/zep-papers#5 showing Zep had
  included Category 5 in the numerator but excluded it from the denominator; re-running
  Zep's own pipeline with the fix gives **58.44% ± 0.20**. Zep edited the post and conceded
  the arithmetic. https://github.com/getzep/zep-papers/issues/5
- **Letta:** 74.0% on LoCoMo with GPT-4o-mini using a file and `grep` — beating Mem0's best
  reported configuration (https://www.letta.com/blog/benchmarking-ai-agent-memory, as
  summarised in the Zep and Hidden Layer write-ups).
- **Benchmark-quality audit:** Penfield Labs found 99 score-corrupting errors in LoCoMo's
  1,540 questions (6.4%), putting a theoretical ceiling of ~93.6% on the published
  benchmark, and their adversarial stress test showed the standard LoCoMo judge accepts
  **62.81%** of intentionally wrong-but-topical answers.
  https://essays.bloo-mind.ai/posts/2026-05-20-mem-eval/ (the full, citable teardown of the
  Mem0–Zep–Letta dispute; also documents Mem0's own reproducibility issues #3004/#3944).
- **MemoryBench** (Tsinghua, arXiv:2510.17281): *"none of the advanced memory-based LLMsys
  (i.e., A-Mem, Mem0, or MemoryOS) can consistently outperform RAG baselines"*, and Mem0
  **could not finish two of their task types** because its memory pipeline could not process
  the input in reasonable time. Reported via the Hidden Layer essay; I did not read the
  paper's tables directly.
- **Third-party local latency/quality run** (effloow, 2026-09-26, one 419-turn LoCoMo
  conversation, 92 answerable + 28 adversarial probes, one shared answering model):
  Mem0 2.1.0 search **38 ms p50**, ingest **1.52 s/turn**, 353 retrieved tokens,
  **judge recall 50.0%**; Letta 0.11.7 318 ms / 0.37 s / 503 tok / **52.2%**;
  Zep-Graphiti 0.30.2 163 ms / 3.65 s / 212 tok / 37.0%. They explicitly refuse to compare
  that with the managed 0.88 s / 92.5 figure.
- **Another third party** puts Mem0 at **49%** on LongMemEval with GPT-4o against Zep's
  63.8% (https://agentmarketcap.ai/blog/2026/04/11/agent-memory-architecture-production-2026).
- **Mem0 concedes the mechanism**: *"Independent benchmark results can differ by a wide
  margin, because of the dataset, the model, the retrieval depth, the configuration, and the
  grading method"*, and their own list of score-moving variables includes retrieval depth,
  reranking config, judge model, and *"Recall grading against end-to-end answer grading …
  Recall-only scores are systematically higher"*
  (https://mem0.ai/blog/state-of-ai-agent-memory-2026).

### How this compares with our numbers — carefully

mem0's 94.4 and our 0.761 are **not** the same measurement, and the honest list of
differences is long: they use an LLM judge (GPT-5) we use LongMemEval's own checker; they
use a frontier answerer (GPT-5) we use a local quantized MoE; their retrieval budget is
**top-200 memories ≈ 6,787 tokens**, ours is **96 notes / 24,000 characters** (roughly the
same token mass, ~2× the item count); they report a single-pass retrieval, we report an
oracle retrieval measurement × 3 repeats at temp 0.8.

Two things *do* line up and are worth citing in our own docs:

1. **Budget is not the lever, and they measured it too.** Their LongMemEval score is
   **94.8 at top-50 vs 94.4 at top-200** (memory-benchmarks README) — 4× the retrieval
   depth buys −0.4. That is an independent echo of our measured "a bigger injection budget
   = zero gain".
2. **Their number (94.4 vendor / 88.6–91.0 OSS) sits far above every third-party
   reproduction (38–75%), while our 0.761 sits inside the third-party band.** Given that
   the answer model dominates (their words), our figure is not evidence that tier-3 is
   better or worse than mem0 — it is evidence that **our measurement discipline is closer
   to what independent auditors reproduce than the vendor leaderboard is**.

---

## What mem0 does better than Strata tier-3

1. **An entity index as a third retrieval signal, with an explicit popularity dampener.**
   *Where*: `mem0/memory/main.py:605-650` (`_upsert_entity`, exact-text match then cosine
   ≥ **0.95** semantic match for dedup, `:621`), `:1100-1204` (batch entity linking on
   write), `:1747-1827` (boost on read), `mem0/utils/entity_extraction.py:1-16`
   (PROPER / QUOTED / TOPIC / IDENTIFIER via spaCy NER + hand rules for technical
   identifiers, quoted text, compound noun phrases, with a `_GENERIC_HEADS` stop list at
   `:33+` and span-overlap resolution at `:699-730`). The dampener is the interesting bit:
   `memory_count_weight = 1/(1+0.001·(n−1)²)` (`:1816`).
   *Why it matters to us*: this is the missing answer to "95% of rows sit on shelves too
   coarse to navigate". It routes by **what the query is about**, not which shelf it lives
   on, and it degrades gracefully — a hub subject stops boosting instead of boosting
   everything. We compute the same rare-token subject signal (`df ≤ 3`) but only at write
   time, in the merge law; we throw it away at read time.

2. **Extraction context: the last-k turns plus a two-date temporal frame.**
   *Where*: Phase 0 `get_last_messages(session_scope, limit=10)` (`main.py:922`,
   `storage.py:298`), the `Last k Messages` / `Observation Date` / `Current Date` sections
   (`prompts.py:519-541`), and the explicit anti-contamination rule
   (`prompts.py:689`).
   *Why it matters to us*: `tools/curate_loop.py` extracts **one muted generation per
   turn**, so a turn saying "and then she moved to Omaha" has no antecedent and no date
   anchor. Our facts inherit that. Their prompt text for temporal grounding is also
   directly reusable.

3. **Query-length-adaptive score normalisation.**
   *Where*: `mem0/utils/scoring.py:16-55` (`get_bm25_params` → `(midpoint, steepness)`
   table) + `normalize_bm25` sigmoid.
   *Why it matters to us*: our `serve/memory.py:1030` comment records that the first
   hybrid version "blended raw cosine into the bm25 blend" and got the scales wrong; we
   fixed it by abandoning score fusion for RRF. Their approach recovers score-level
   fusion (which composes with confidence/recency terms; rank-level does not) by making the
   lexical scale a function of query length. Worth an A/B, not a rewrite.

4. **A consolidation layer with *queryable* lifecycle state.**
   *Where*: `lifecycle_state` + `replacement_memory_id` as **indexed columns** with
   `latest_only` / `include_merged` read flags, plus `immutable` / `exclude_from_dream`
   per-row exemptions, a dashboard review surface, and a REST activity feed
   (`docs/platform/features/dream.mdx`, `docs/api-reference/dream/*.mdx`, the Dream blog).
   *Why it matters to us*: our `invalid_at`/`superseded_by` pair (`serve/memory.py:376-379`)
   is the same idea but is not exposed as a read mode ("give me current truth only") and
   has **no per-row exemption flag** — we approximate it with the "never auto-retire a row
   with `hits>0`" rule (`serve/memory.py:297`) and human review. A named state + an opt-out
   bit is a cleaner contract, and it is what makes their review loop tractable.

5. **Instrumented retrieval: `explain=True` and in-band scale notices.**
   *Where*: `score_details` with every component and the threshold used
   (`mem0/utils/scoring.py:127-133`, plumbed through `search(explain=…)`), and
   `mem0/memory/notices.py:117-127` (`SCALE_MEMORY_COUNT_THRESHOLD = 2000`,
   `SCALE_TOP_K_THRESHOLD = 50`, `PERFORMANCE_SLOW_QUERY_THRESHOLD_SECONDS = 2.0`) firing
   from `add`/`search`/`get_all`/`delete_all`.
   *Why it matters to us*: our `tools/lme_retrieval_ab.py` has to infer which lever moved
   (and exits 2 when the hit lists are identical). Per-candidate score decomposition in the
   *product* (not just the harness) turns "why did this row win" from a debugging session
   into a field.

6. **Provider breadth and graceful degradation as a contract.**
   *Where*: 28 vector stores, 16 with `keyword_search`, `base.py:68-76` returning `None`
   when unsupported, an explicit init-time warning when the chosen store has no keyword
   support (`main.py:543-549`), and a documented degradation matrix — no spaCy → semantic
   only; no fastembed → no BM25; entity store down → semantic + BM25
   (`docs/migration/oss-v2-to-v3.mdx` "Graceful Degradation").
   *Why it matters to us*: our opt-in bge-small ONNX embedder already keeps the default
   path byte-identical; formalising "which signals are live right now" as a startup log
   line + a `/memory` stats field is the same discipline at near-zero cost.

7. **Anti-hallucination id remapping in prompts.**
   *Where*: `main.py:933-940` — existing memories are re-labelled `"0","1",…` for the LLM
   and mapped back through `uuid_mapping`.
   *Why it matters to us*: our curation loop asks the model to reference existing rows
   (supersede targets, class choices). Integer slots are a cheaper failure mode than
   19-digit ids, and unmappable references are detectable.

8. **Attribution as a schema field, not prose.** `attributed_to: user|assistant` is a
   required output field (`prompts.py:935`), stored (`main.py:1037-1038`), and carried into
   the history table (`actor_id`, `role`). Plus `name`-based multi-speaker attribution
   (`main.py:897-905`). We record `source` and `provenance` but do not distinguish
   "user said it" from "assistant suggested it" in a filterable column — which matters for
   the confabulation-vs-idk line our cold-scorer already depends on.

9. **Operational packaging.** Self-hosted FastAPI server + dashboard with auth and a
   one-command `make bootstrap` (`server/README.md`), OpenAI-compat endpoints, typed
   exceptions with `error_code` + `suggestion` (`mem0/exceptions.py`), a CLI that mints an
   API key without email/OTP for agent callers (`docs/llms.txt`), MCP server, 21 framework
   integrations, editor plugins that hook prompt-submit/file-read/pre-compact events
   (`docs/changelog/highlights.mdx`, Kimi entry: hooks on prompt submit, file reads, Bash
   output, stop, pre-compact; direct `Write`/`Edit` to memory files blocked). Our MCP +
   web UI work has the same shape; their capture-hook taxonomy is worth stealing wholesale.

10. **A published, runnable harness with per-question artefacts.**
    https://github.com/mem0ai/memory-benchmarks — ingest→search→answer→judge, `cutoff_results`
    at top_10/20/50/200 per question, a Next.js results UI for inspecting retrieval per
    question, and multi-provider configs including `configs/ollama.yaml` (fully local).
    Their harness is OSS-only-comparable and they say so; ours (`tools/lme_harness.py`) is
    stricter on repeats and temperature but has no public UI.

---

## What Strata does that mem0 does not

1. **A deterministic merge law where cosine only proposes.** Subject veto on rare tokens →
   payload containment → Jaccard drift bar (≥0.75 on content tokens) → supersede only when
   the new row is strictly richer (`serve/memory.py:502-590`, `:772-830`,
   `tools/m2_merge.py`). mem0 OSS has **no semantic dedup at all** — MD5 only
   (`main.py:1022`), which is precisely what issues #4896/#5867/#6515 are about. mem0
   Platform's Dream reaches a similar *outcome* but the decision is LLM-made and closed.
2. **A utility test before storage.** Our curation loop cold-scores every candidate with
   three muted probe passes and sorts it FILL / STABILIZE / CORRECT / DON'T-STORE, so a
   fact the weights already own is logged, not stored (`tools/curate_loop.py:8-20`).
   mem0 stores whatever the extractor emits — "when in doubt, extract"
   (`prompts.py:578`) — and their 32-day production audit found **97.8% junk** as a
   result (#4573). Nobody in this market has a mechanism like ours.
3. **Curation off the hot path, on engine idle.** Our loop drains pending turns and is
   gated on `GET /v1/status` not-busy (`tools/curate_loop.py:30-33`), so interactive
   latency is untouched. mem0 OSS `add()` is synchronous and carries the ~8.4k-token
   prompt every call (measured 1.52 s/turn by effloow; MemoryBench could not finish two of
   its task types in reasonable time).
4. **One box, one file, encrypted, no cloud.** SQLite + FTS5 bm25 + vectors in the same
   db, encrypted at rest, CPU-only ONNX embedder, local LLM. mem0 OSS needs a separate
   vector database (Qdrant/pgvector), defaults to API LLM+embedder, phones home to PostHog
   by default (`telemetry.py:14`), and has **no encryption at rest in code**.
5. **A classification scheme with a rejection rule.** 17 LCC-style shelf marks assigned by
   a keyword map that requires a 2-token margin over the runner-up, else the row stays
   wild (18%). mem0's only topic taxonomy is Platform-only, LLM-chosen from a fixed
   15-label catalog, never re-applied to existing rows, and not available in OSS at all.
6. **Recency and confidence in the ranking blend.** 0.75 rank + 0.25 recency + 0.10
   confidence (`serve/memory.py:860`, `AGE_SCALE_DAYS` at `:51`). mem0 OSS's scoring
   function has **neither** (`scoring.py:60-118`; confirmed independently in #4956). Their
   answer is a cloud feature with a 0.3×–1.5× access multiplier.
7. **Read-path near-dupe suppression and explicit block budgeting.** We suppress duplicate
   facts inside the injected block (`serve/memory.py:772-830`) and cap it at 96 notes /
   24k chars with pinned-per-conversation blocks (`:236-238`, `:1090-1114`), and we
   measured that a bigger budget gains nothing. mem0 returns up to `top_k` rows and the app
   is on its own for de-duplication and layout.
8. **Measurement discipline matched to the harness.** temp 0.8/1.0 (never 0), ≥3 repeats
   for pass/fail, one fresh conversation per ask, memory muted via `X-Strata-Memory: off`,
   `usage.prompt_tokens` asserted against the local render, offline retrieval A/B in ~45 s,
   a lever-must-move guard that exits 2 on identical hit lists. mem0's paper used temp 0
   for baselines and 10 runs for the judge metric; their 2026 numbers are single-config
   vendor runs with a ±1 pt judge CI, and the disputed history (Zep → 75.14 → 58.44;
   Letta's `grep` at 74.0; #3944 at 0.20) is what our protocol is designed to avoid.
9. **Supersede chains you can query as data.** `invalid_at` + `superseded_by` on the row
   itself (`serve/memory.py:376-379`, `:595-601`) supports as-of inspection and
   retirement-audit directly. In OSS mem0 the live row is unversioned and the old value
   exists only as `old_memory` in a separate `history` table (`storage.py:102-126`).
10. **Access-aware eviction with a safety rule.** We track `hits` and, under store-cap
    pressure, evict only **never-recalled** rows (`hits=0`) first
    (`serve/memory.py:474-485`), while explicitly refusing to rank by `hits` because it
    drifts (`:828`). mem0 OSS has no access counter; Platform has one but uses it to
    *rerank*, not to protect rows.

---

## Portable ideas for Strata tier-3

Ranked by value per unit of risk. Everything here is SQLite + Python, no graph database,
and nothing puts an LLM in the interactive hot path.

**1. Subject/entity inverted index with a degree penalty — route reads by what the query
is about.**
*Mechanism*: mem0's entity collection and `_compute_entity_boosts`, especially the dampener
`boost = cos · 0.5 · 1/(1+0.001·(n−1)²)` (`mem0/memory/main.py:1807-1821`) and the
0.95-cosine entity dedup with exact-text precedence (`:621`, `:1166`).
*Mapping*: a `subjects(subject_norm, fact_id, class)` table maintained **by the curation
loop**, not the write path, keyed on exactly the `df ≤ 3` rare-token subject the merge law
already extracts; at read time, one extra query per query-subject, and a bounded additive
boost on rows already in the candidate list (so we keep RRF as the primary order and use
the boost as a tie-break inside the blend at `serve/memory.py:860`). This is also the
concrete fix for "nothing routes a query to a shelf": the subject index tells us which two
shelves matter without touching the 17-shelf map.
*Cost*: zero LLM calls; one table + one `CREATE INDEX`; per read, 1–8 extra indexed
lookups (µs at 2.6k rows, still sub-ms at 100k); index work is O(rows) once, in the loop.
*How we measure it*: `tools/lme_retrieval_ab.py` with arms `subject=off/on` — the
lever-must-move guard already forces a real ranking change; report `turn_top1` and
right-fact-in-block per question type, 3 repeats, temp 0.8/1.0, memory muted for the /v1
arm. Then the store-doubling probe (`tools/m4_reach.py`) to see whether it slows the
measured ~3-point-per-doubling decay. *Risk*: hub subjects (a project name in every row)
boost everything — the degree penalty is what prevents it and must be tuned, not copied
blind; and a boost applied *after* RRF can undo RRF's work if weighted too hard.

**2. Feed the extractor last-k turns + an observation date, and forbid relative dates in
output.**
*Mechanism*: Phase 0 context (`main.py:922`) and the Observation-Date vs Current-Date rule
(`prompts.py:524-535`) with the no-contamination guard (`:689`).
*Mapping*: `tools/curate_loop.py` extraction step — read the previous N journaled turns for
the same scope from the journal table and append them as a `## Preceding turns` block; pass
the turn's own timestamp as `Observation date`; require "convert every relative reference
to an absolute date using the observation date; never use today's date".
*Cost*: prompt tokens only — **no extra asks per turn** (still 1 extraction ask), roughly
+N×~120 tokens per ask, so a few seconds per turn of idle engine time. ~40 lines.
*How we measure it*: (a) a hand-built extraction regression set of ~30 turns with pronouns
and relative dates; count grounded-vs-ungrounded outputs before/after; (b) the
temporal-reasoning slice of the LongMemEval venue (80 min, 3 repeats); (c) right-fact-in-block
on the near-dupe families where the subject is a pronoun. *Risk*: more context = more
contamination — mem0 needed an explicit rule *because* the model imports details from
neighbouring memories; we must keep the "the new turn never mentioned it" clause, and the
longer prompt eats engine time we budget for cold-scoring.

**3. Turn mem0's junk taxonomy into deterministic pre-filters, before we burn 4 asks.**
*Mechanism*: not a mechanism in their code but in their failure data — #4573's category
table (boot/system-prompt restating 52.7%, heartbeat/cron 11.5%, architecture dumps 8.2%,
transient task state 7.4%, hallucinated profiles 5.2%, identity confusion 3.3%, "recalled
memories get re-extracted and stored as new entries"), plus the message-level
`isNoiseMessage()` layer from PR #4302.
*Mapping*: in `tools/curate_loop.py`, before the cold-score stage: drop turns whose content
matches a system/boot/identity block; drop heartbeat/cron/`NO_REPLY` shapes; drop
candidates whose rare-token subject + payload head already exists live (we already compute
both); and mark any candidate that is a near-copy of a fact **we injected into that turn**
as recalled-context, not new evidence.
*Cost*: zero LLM; it *removes* asks — on their numbers, potentially half the curation load.
*How we measure it*: replay a stored journal batch and report rows-stored-per-turn before
and after; then the right-fact-in-block probe, which is the metric that actually moves when
junk stops consuming block slots (our own decay is ~3 points per store doubling, so
shrinking the store should *raise* it). Keep a `would_have_dropped` log for the review list
and audit 100 of them by hand. *Risk*: a false-positive filter silently loses real facts;
mitigated by logging every drop with its rule id.

**4. Lifecycle state as a named, indexed column + `latest_only` read mode + per-row
curation exemption.**
*Mechanism*: Dream's `lifecycle_state` / `replacement_memory_id` indexed columns,
`latest_only` / `include_merged` flags, `immutable` / `exclude_from_dream` opt-outs
(`docs/platform/features/dream.mdx`, Dream blog).
*Mapping*: derive a `state` column (active / merged / superseded / expired) from our
existing `invalid_at`/`superseded_by`, index it, thread `latest_only` through
`serve/memory.py:644-664` and the `memory_search` tool, and add an `exclude_from_curation`
bit so human-ratified rows are untouchable — replacing the indirect `hits>0` proxy at
`serve/memory.py:297`.
*Cost*: one `ALTER`, one index, filter plumbing, no LLM.
*How we measure it*: the LongMemEval **knowledge-update** arm of
`tools/lme_retrieval_ab.py` — that is where our known −0.139 `turn_top1` regression lives,
and "hide the dead value" is the direct hypothesis; 3 repeats, temp 0.8/1.0. Also a
replayed-log count of "superseded value surfaced inside the block" before/after.
*Risk*: temporal-reasoning questions sometimes *need* the old value ("where did you work
before?"), which is exactly why mem0 keeps superseded rows visible by default and makes
`latest_only` opt-in — ship it as a flag, never as a new default.

**5. `explain` + in-band health notices in the product, not just the harness.**
*Mechanism*: `score_details` (`scoring.py:127-133`) and the threshold notices
(`mem0/memory/notices.py:117-127`, 2,000 rows / top_k ≥ 50 / >2 s).
*Mapping*: an `explain` flag on `memory_search` returning the blend decomposition per hit
(rank, recency, confidence, class-match, subject-boost), and a stats banner in the web UI
when live rows cross a threshold or a recall exceeds a latency budget — our own numbers
(2,521 dev rows, 2,655 store) are already at their notice level.
*Cost*: ~40 lines; no LLM; slightly larger tool payload.
*How we measure it*: not accuracy — diagnosis time. Validate mechanically: assert the
reported components sum to the shipped score, and that an A/B arm's `explain` output
matches the offline harness's per-arm weights. *Risk*: exposing internal scores in the tool
result can leak into model output; keep it off by default.

**6. Integer-slot id remapping for every prompt that references store rows.**
*Mechanism*: `main.py:933-940` (`uuid_mapping`, ids re-labelled "0","1",…).
*Mapping*: the curation loop's supersede/class prompts — show slot numbers, map back, and
count unmappable references.
*Cost*: ~15 lines. *How we measure it*: log invalid row references for one week of curation
before and after; a free reliability number. *Risk*: none material.

**7. Query-length-adaptive lexical normalisation, A/B'd against our RRF.**
*Mechanism*: `get_bm25_params` + `normalize_bm25` (`scoring.py:16-55`).
*Mapping*: an alternative arm in `serve/memory.py:1043-1058` — normalise the FTS5 bm25 arm
by query length, then fuse additively with the vector arm and let the existing
recency/confidence terms compose (they cannot compose with ranks).
*Cost*: ~15 lines, no LLM, no index work. *How we measure it*: one
`tools/lme_retrieval_ab.py` run with arms {rrf k=60, rrf k=10, normalised-additive},
per-question-type split, 3 repeats; the knowledge-update split is the one to read first.
*Risk*: additive fusion re-opens the scale-mixing problem we already got burned by — and we
must **not** copy mem0's semantic-score gate (`scoring.py:111`), which makes lexical-only
matches unreachable and is a documented limitation, not a feature
(`docs/migration/oss-v2-to-v3.mdx`: "BM25 is a boost signal, not a recall expander").

**8. Feedback as an API, feeding confidence.**
*Mechanism*: `client.feedback(memory_id, POSITIVE|NEGATIVE|VERY_NEGATIVE, reason)`
(`docs/platform/features/feedback-mechanism.mdx`, `docs/api-reference/memory/feedback.mdx`).
*Mapping*: a `/memory` op writing per-row feedback counts that adjust the `confidence`
column our blend already weights at 0.10 (`serve/memory.py:860`).
*Cost*: one column + one endpoint; no LLM. *How we measure it*: does the rank of a
feedback-flagged row move as predicted (an offline probe, not a venue run); then whether
right-fact-in-block improves on the probed families. *Risk*: sparse, self-selected signal;
bound the adjustment so one click cannot pin a row.

**9. Scheduled expiry as a first-class write parameter.**
*Mechanism*: `expiration_date` — UTC, inclusive, fail-open on a bad value, hidden not
deleted, `show_expired` to inspect (`main.py:427-458`, `docs/platform/features/memory-expiration.mdx`).
*Mapping*: we have `invalid_at` (retired by evidence) but no "this fact was dated, stop
surfacing it after D" — useful for the transient-task-state junk #4573 identifies.
*Cost*: one column + one predicate in the search filter. *How we measure it*: a probe set
of dated facts (deadlines, trial ends, temporary assignments); measure stale-surfacing rate
with the flag off/on. *Risk*: a fact that "expires" but was the only evidence for a later
decision — keep the row visible to the review list, exactly as they do.

**10. Capture-hook taxonomy for the MCP/plugin path.**
*Mechanism*: their editor plugins fire on prompt submit, file reads, Bash output, stop, and
**pre-compact**, and block direct `Write`/`Edit` to memory files
(`docs/changelog/highlights.mdx`, Kimi/2026-08-13 entry).
*Mapping*: our journalling currently captures conversation; the pre-compact hook is the one
we lack, and compaction is exactly when a harness throws away the turns our curation loop
would otherwise lose.
*Cost*: one hook + one journal write. *How we measure it*: journaled-turn count and
`turns_pending` before/after per session; then whether curation's yield per session rises.
*Risk*: duplicate capture (pre-compact + stop) re-introduces the re-extraction feedback
loop from #4573 — dedup on turn id.

### Explicitly not portable

- **A graph database.** mem0 deleted ~4,000 lines of graph drivers from OSS and moved
  graph memory to closed cloud; the paper's own `Mem0ᵍ` **lost to plain Mem0 on multi-hop**
  (47.19 vs 51.15 J). Strata's shelf + subject-index plan is strictly cheaper.
- **ADD-only accumulation as a write law.** Their docs concede it: knowledge-update is the
  hardest category for an ADD-only architecture, *"older facts are preserved rather than
  overwritten, so semantically similar prior facts can still surface alongside newer ones"*
  (`docs/core-concepts/memory-evaluation.mdx`). Our supersede law is the counter-design, and
  our own −0.139 knowledge-update result says the *tuning* is the hard part, not the law.
- **The 8.4k-token extraction prompt.** On a 24 GB GPU shared with a quantized 125B MoE,
  ~8.4k prompt tokens per write is the difference between a 2-minute daily loop and a
  20-minute one. Steal the *rules* (no-echo, no-meta-extraction, no-contamination,
  preserve proper nouns, observation date), not the length.
- **Their default fusion gate** (semantic-score threshold applied before fusion) — see #7.
- **MD5 hash dedup as the only dedup** — strictly weaker than what we already run.
- **Dream / Decay / Temporal Reasoning as code** — closed, cloud, per-project toggles
  (`docs/platform/platform-vs-oss.mdx` lists all three as "Not supported" in OSS; passing
  `decay` raises).

---

## Search log

Queries issued 2026-10-14 through the Brave Search MCP (`brave_web_search`; the first five
returned empty result sets and were re-issued against the same tool with a different
result shape — both are logged). 22 queries + 8 direct page/repo fetches.

1. `mem0 memory layer architecture LLM extract ADD UPDATE DELETE NOOP`
2. `Mem0 paper Building Production-Grade Memory Layer arxiv`
3. `mem0 vs Zep vs Letta benchmark LOCOMO comparison`
4. `mem0 LOCOMO benchmark disputed criticism accuracy`
5. `mem0 graph memory mem0g Neo4j`
6. `mem0 memory layer architecture ADD UPDATE DELETE NOOP`
7. `Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory`
8. `mem0 vs Zep vs Letta benchmark LOCOMO comparison`
9. `mem0 LOCOMO benchmark disputed criticism accuracy`
10. `mem0 graph memory mem0g Neo4j`
11. `mem0 LOCOMO benchmark numbers 26% improvement 91% lower latency`
12. `mem0 benchmark criticism Zep claims rebuttal flawed evaluation`
13. `mem0 LongMemEval score`
14. `mem0 platform pricing managed memory layer docs`
15. `mem0 procedural memory semantic episodic`
16. `mem0 github issues memory loss duplicate memories problem`
17. `mem0 OSS no update delete consolidation memories accumulate contradictions`
18. `mem0 latency cost per add LLM calls tokens overhead`
19. `independent evaluation mem0 open source memory benchmark reproduce 2026`
20. `mem0 local llama ollama self-hosted offline no cloud`
21. `mem0 agent memory leaderboard shared pipeline mem0 zep letta`
22. `mem0 run_id app_id session scoping docs entity-scoped memory`
23. `mem0 encryption at rest GDPR data deletion security self-hosted SOC2`
24. `mem0 pricing retrieval requests per month limits hobby`
25. `mem0 Dream consolidation supersede merge platform feature blog`
26. `mem0 how many LLM calls per add search cost analysis open source`
27. `mem0 injection top_k 20 memories context window agent integration pattern`
28. `mem0 community feedback production experience reliability complaints`

Direct fetches / reads: https://arxiv.org/abs/2504.19413 ·
https://arxiv.org/html/2504.19413v1 (full text, Tables 1–2) ·
https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/ ·
https://essays.bloo-mind.ai/posts/2026-05-20-mem-eval/ ·
https://effloow.com/articles/mem0-zep-letta-agent-memory-latency-benchmark-2026 ·
https://mem0.ai/blog/state-of-ai-agent-memory-2026 ·
https://mem0.ai/blog/dream-background-memory-consolidation-for-ai-agents ·
https://raw.githubusercontent.com/mem0ai/memory-benchmarks/main/README.md ·
GitHub `issue_read` for mem0ai/mem0 #4896, #5867, #4956 (+20 comments), #4573, #6515,
#5330, #3004, #3944 and getzep/zep-papers #5 (via search snippet).
Repo files read in full or in relevant part: `mem0/memory/main.py` (add, search, get_all,
update/delete, entity store, init), `mem0/utils/scoring.py`,
`mem0/utils/lemmatization.py`, `mem0/utils/entity_extraction.py` (header + candidate
rules), `mem0/memory/storage.py`, `mem0/configs/prompts.py` (additive + legacy update
prompts), `mem0/configs/base.py`, `mem0/configs/enums.py`, `mem0/vector_stores/base.py`,
`mem0/vector_stores/qdrant.py`, `mem0/memory/notices.py`, `mem0/memory/telemetry.py`,
`mem0/reranker/*.py`, `server/main.py`, `server/README.md`,
`docs/core-concepts/how-it-works.mdx`, `docs/core-concepts/memory-evaluation.mdx`,
`docs/migration/oss-v2-to-v3.mdx`, `docs/platform/platform-vs-oss.mdx`,
`docs/platform/features/{dream,memory-decay,temporal-reasoning,graph-memory,advanced-retrieval,feedback-mechanism,custom-categories,memory-expiration}.mdx`,
`docs/open-source/configuration.mdx`, `docs/open-source/features/reranker-search.mdx`,
`docs/changelog/highlights.mdx`, `docs/llms.txt`, `README.md`,
`mem0-ts/src/oss/src/{memory/index.ts,prompts/index.ts}`, `tests/utils/test_scoring.py`.

---

## Unverified claims

1. **I never ran mem0.** Everything about behaviour is read from code, docs, issues, and
   third-party write-ups. The write-latency (1.52 s/turn), search-latency (38 ms p50), and
   judge-recall (50.0%) figures are effloow's single 419-turn run on Mem0 2.1.0, not mine,
   and not on 2.2.1.
2. **Platform behaviour is documented, not inspectable.** Dream's exact merge/supersede
   decision procedure, the temporal-reasoning metadata schema, the decay factor curve, and
   the "proprietary optimizations not available in the open-source SDK" are all closed.
   Their claim that merge requires "all the information in the old one" is a blog/doc
   statement; there is no code to check. The apparent structural similarity to our merge
   law is inference from prose.
3. **Which OSS release maps to which benchmark.** The 94.4/92.5 numbers are labelled
   Platform; the 88.6–91.0 numbers are labelled OSS with a SageMaker Qwen-600M embedder —
   neither is `mem0ai==2.2.1` with our stack. Nobody has published LongMemEval on the
   current OSS default (`gpt-5-mini` + `text-embedding-3-small` + Qdrant + fastembed).
4. **MemoryBench's Mem0 result** ("cannot consistently outperform RAG baselines", "could
   not finish two task types") is quoted from the Hidden Layer essay's reading of
   arXiv:2510.17281; I did not read that paper's tables or its mem0 configuration.
5. **The 49% LongMemEval figure** (agentmarketcap.ai) and the 74.0% Letta-filesystem figure
   are third-party/second-hand; the Letta number predates mem0's April 2026 algorithm, and
   the agentmarketcap run does not state its mem0 version or embedder in the snippet I read.
6. **Docs-vs-docs drift inside mem0's own repo** — 91.6/93.4 (migration guide) vs
   92.5/94.4 (README + evaluation page) — is a real discrepancy I could not resolve; I did
   not find a changelog entry reconciling them. Treat the README/evaluation numbers as
   "current" and the migration numbers as stale.
7. **`linked_memory_ids` being dropped** is a positive reading of two code paths
   (`main.py:1015-1045` and `mem0-ts/.../memory/index.ts:1026-1039`) plus a repo-wide grep
   that shows the only uses of the field are entity-store bookkeeping. The **Platform**
   pipeline may well persist it — that is exactly the "proprietary optimizations" gap, and
   the graph-memory doc implies links exist server-side.
8. **Whether the four closed PRs on #4956 were rejected or just abandoned** — I saw
   `closed_by_pull_requests` listing #4965, #4969, #5240, #7301 with state CLOSED, but did
   not open each PR to check merge status or maintainer response. The issue itself is open
   as of 2026-10-06.
9. **Pricing tiers** (Hobby 10k adds / 1k retrievals, Pro $249/mo for graph + unlimited
   retrieval) come from third-party trackers (memnode, hydradb, usagepricing) plus
   https://mem0.ai/pricing; I did not log into the dashboard to confirm the current
   entitlement matrix, and the pricing page snippet I fetched advertises "unlimited
   retrieval" without a tier label.
10. **Strata-side line numbers** quoted here (`serve/memory.py:297`, `:376-379`, `:502`,
    `:860`, `:1043`, `:1090`, `:236-238`, `tools/curate_loop.py:8-20`, `:30-33`) were
    re-grepped in this repo today and are correct as of now — but they are the moving part
    of this document, and the mapping of each portable idea to a specific call site should
    be re-checked at implementation time.
11. **Our 0.761 vs their 94.4 is not a controlled comparison** — different judge, different
    answerer, different retrieval budget shape (96 items vs 200), different dataset variant
    and checker. The only defensible cross-reads are stated in the Evaluation section:
    budget-invariance (their top-50 ≈ top-200) and the vendor-vs-auditor spread.
