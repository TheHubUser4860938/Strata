# Graphiti (getzep/graphiti) — deep read for Strata tier-3

Researched 2026-10-14 against `main` @ `7f989075b2eae0ad4db938e4f703f3b0701d73f3`
("chore: set graphiti-core version to 0.31.0 for the v0.31.0 release (#2010)", tagged
2026-10-14), shallow-cloned to `/tmp/opencode/research/graphiti` (30 MB, 40,773 LOC in
`graphiti_core/`, 1,021 commits, 31.8k stars / 3.3k forks, Apache-2.0).
Repo: https://github.com/getzep/graphiti · paper: https://arxiv.org/abs/2501.13956
· docs: https://help.getzep.com/graphiti

Line numbers below are from that clone and are exact; line numbers quoted *inside*
issue text are from other commits and are marked as such.

---

## What it is

Graphiti builds a **temporal context graph**: a directed graph of entity nodes, fact
edges, episode nodes and community nodes, in which every fact carries a *validity
window* and every derived fact points back at the raw data that produced it
(https://github.com/getzep/graphiti README, "What is a Context Graph?").

The paper describes three hierarchical subgraphs (§2, https://arxiv.org/html/2501.13956v1):

| Tier | Contents | Code |
| --- | --- | --- |
| Episode subgraph 𝒢e | raw messages / text / JSON, non-lossy ground truth | `graphiti_core/nodes.py::EpisodicNode` |
| Semantic entity subgraph 𝒢s | entity nodes + fact edges (triplets) with validity windows | `nodes.py::EntityNode`, `edges.py::EntityEdge` |
| Community subgraph 𝒢c | clusters of strongly connected entities + summaries | `community_operations.py` |

The **fact edge** is the unit that matters for Strata. `graphiti_core/edges.py:263-289`:

```python
class EntityEdge(Edge):
    name: str = Field(description='name of the edge, relation name')
    fact: str = Field(description='fact representing the edge and nodes that it connects')
    fact_embedding: list[float] | None = ...
    episodes: list[str] = ...            # provenance: which episodes produced it
    expired_at: datetime | None = ...    # when the DB stopped holding it
    valid_at: datetime | None = ...      # when it became true in the world
    invalid_at: datetime | None = ...    # 'datetime of when the fact stopped being true;
                                         #  soft deadlines are not invalid_at'
    reference_time: datetime | None = ...
    attributes: dict[str, Any] = ...     # typed, Pydantic-defined payload
```

So a Graphiti fact is *not* a sentence: it is a sentence **plus** two endpoints, a
relation name, a provenance list, four timestamps and an optional typed payload.
Strata's row is a sentence plus confidence/class/one retirement pointer
(`serve/memory.py:328-339`, `:376-379`).

**Bi-temporal** is the headline property (paper §2.1, §2.2.3; blog
https://www.getzep.com/blog/beyond-static-knowledge-graphs/): timeline `T′` =
`created_at` / `expired_at` (transaction time), timeline `T` = `valid_at` /
`invalid_at` (world time). "Nothing is deleted" is real in both systems: a retired
edge stays in the graph carrying `invalid_at`.

Graphiti ≠ Zep. Graphiti is the OSS framework and you bring your own graph database;
Zep is the managed product on a proprietary "Context Graph Engine" claiming sub-200 ms
retrieval (README "Zep vs Graphiti" table; https://www.getzep.com/platform/context-graph-engine/).
Zep Community Edition was discontinued in favour of Graphiti
(https://www.getzep.com/blog/announcing-a-new-direction-for-zeps-open-source-strategy/).

---

## Write path (incremental construction)

`Graphiti.add_episode()` (`graphiti_core/graphiti.py:1332`, body at `:1440-1530`) is
synchronous and fully LLM-driven. Ordered steps, with the code that runs them:

1. **Fetch context** — the last *n* episodes (`retrieve_episodes`, `graphiti.py:1249`).
   Paper §2.2.1 fixes n=4 ("two complete conversation turns").
2. **Extract nodes** — `extract_nodes` (`utils/maintenance/node_operations.py:73`),
   **one LLM call** per episode (`_call_extraction_llm`, `:299-326`; prompt
   `prompts/extract_nodes.py`). Speaker is always extracted (paper §6.1.1).
3. **Resolve nodes (dedupe)** — `resolve_extracted_nodes` (`:816`). This is the part
   Strata should study: it is **deterministic first, LLM only on residue**. See below.
4. **Extract edges** — `extract_edges` (`edge_operations.py:134`), **one LLM call**
   producing `ExtractedEdges` (prompt `prompts/extract_edges.py::edge`), each edge
   carrying `fact`, `relation_type`, `valid_at`, `invalid_at`.
5. **Resolve edges** — `resolve_extracted_edges` (`:424`): two hybrid searches per
   edge (pair-scoped for duplicates, unscoped for contradictions), then **one LLM
   judge call per edge**, then optional attribute + timestamp calls.
6. **Hydrate entities** — `extract_attributes_from_nodes` →
   `_extract_entity_summaries_batch` (`node_operations.py:1088`): summaries are
   updated by *appending edge facts with no LLM* when short enough
   (`:1128-1134`), otherwise batched into "flights" of `MAX_NODES = 30`
   (`node_operations.py:66`) — one LLM call per flight.
7. **Persist** — episode node, `MENTIONS` episodic edges, saga chain
   (`HAS_EPISODE` / `NEXT_EPISODE`), entity edges.

**LLM-call budget per episode** (counted from the code above):
`2 (node+edge extraction) + ~2 × E_edges (dedupe judge + timestamp) + ⌈N_nodes/30⌉
(summaries) + optional attribute calls`. With 5 facts and 6 entities that is ≈13 calls
per episode. An independent harness measured **3.65 s ingest per turn** self-hosted
(https://github.com/hamza-dev-tech/agent-memory-bench). A third-party report on a
Graphiti-derived system measured "about 28 LLM calls per 30k-character part, at about
8.7k input tokens per call" (https://github.com/GetKlai/klai/pull/1745).

**Concurrency** is the throughput lever: `SEMAPHORE_LIMIT` (`helpers.py:43-47`,
`DEFAULT_SEMAPHORE_LIMIT = 20` in code; the README documents the default as **10** —
a doc/code mismatch) and `add_episode_bulk` (`graphiti.py:1586`), which routes through
`utils/bulk_utils.py:263` for combined extraction.

**Dedup is where Graphiti is genuinely ahead of its reputation.**
`utils/maintenance/dedup_helpers.py` is a pure-function, no-LLM resolution ladder:

- `_normalize_string_exact` (`:40`) — lowercase + whitespace collapse; exact-name map
  lookup runs **first, for every name** (`_resolve_with_similarity`, `:321-360`).
- Ambiguity is not silently merged: `if len(existing_matches) > 1:` → escalate to LLM (`:345-349`).
- `_has_high_entropy` (`:80`) — Shannon entropy over characters, threshold `1.5`
  (`_NAME_ENTROPY_THRESHOLD`, `:30`); short/low-entropy names are *refused* the fuzzy
  path and escalated instead.
- MinHash + LSH: 32 permutations, band size 4 (`:33-34`), 3-character shingles
  (`_shingles`, `:89`), `_jaccard_similarity` (`:132`), accept at
  `_FUZZY_JACCARD_THRESHOLD = 0.9` (`:32`).
- `_resolve_with_identity_properties` (`:259`) — if the developer declares an identity
  property on a Pydantic entity type, an exact normalised identity-key match resolves
  the node with **no model and no embedding at all**, choosing the oldest candidate
  deterministically (`matches.sort(key=lambda c: (c.created_at, c.uuid))`, `:286`).
- Only the residue goes to `_resolve_with_llm` (`node_operations.py:649`).

Edges have the analogous fast path: `_dedupe_extracted_edge` short-circuits on
exact-normalised fact text *and* identical endpoints before any prompt
(`edge_operations.py:940-948`), plus a same-batch dedup that also folds reverse
directions with equal `name` (`:497-517`).

**They are actively removing the generative LLM from dedup.** v0.31 ships
`llm_client/gliner2_client.py` (NER model), `llm_client/jev_client.py` and
`llm_client/openai_decisions_client.py` — *decision* models that "answer typed
questions with probabilities. It does not generate text" (README "Jev dedupe";
`spec/jev-decision-client.md`; duplicate threshold 0.7, contradiction threshold 0.8).
That is Strata's position arriving at Graphiti from the other direction.

---

## Temporal edge invalidation — mechanism, in code

Split the mechanism in two, because Graphiti splits it, and most write-ups do not:

**(a) The *judgement* "these two facts contradict" is an LLM call.**
`_dedupe_extracted_edge` (`edge_operations.py:864`) builds one prompt with two indexed
fact lists and gets back `EdgeDuplicate{duplicate_facts: list[int],
contradicted_facts: list[int]}` (`prompts/dedupe_edges.py:25-34`) from
`ModelSize.small` (`edge_operations.py:982-990`). The prompt's system line is
"NEVER mark facts as contradicted unless the new fact explicitly makes the old fact
false" and it carries three worked examples (`prompts/dedupe_edges.py:45-115`).
Indices are range-validated after the fact (`:1013-1035`), and the two lists are
mapped back through an offset (`:1029-1035`).

**(b) The *action* — who gets stamped with what — is deterministic date arithmetic.**
`_apply_temporal_invalidation` (`edge_operations.py:715-746`):

```python
now = utc_now()
if resolved_edge.invalid_at and not resolved_edge.expired_at:
    resolved_edge.expired_at = now            # born already historical
if resolved_edge.expired_at is None:
    invalidation_candidates.sort(key=lambda c: (c.valid_at is None, ensure_utc(c.valid_at)))
    for candidate in invalidation_candidates:
        if candidate_valid_at_utc > resolved_edge_valid_at_utc:
            resolved_edge.invalid_at = candidate.valid_at
            resolved_edge.expired_at = now    # the NEW edge is the one retired
            break
return resolve_edge_contradictions(resolved_edge, invalidation_candidates)
```

and `resolve_edge_contradictions` (`:748-784`), whose whole body is date comparisons:

```python
if (edge.invalid_at <= resolved_edge.valid_at) or (resolved_edge.invalid_at <= edge.valid_at):
    continue                                   # windows already disjoint — no action
elif edge.valid_at < resolved_edge.valid_at:
    edge.invalid_at = resolved_edge.valid_at   # RETIRE the older fact
    edge.expired_at = edge.expired_at or utc_now()
    invalidated_edges.append(edge)
```

Note the asymmetry: the *direction* of supersession is decided by `valid_at` order,
not by recency of ingestion — that is the point of the bi-temporal model, and the
blog walks through the divorce-then-marriage-episode example that motivates it
(https://www.getzep.com/blog/beyond-static-knowledge-graphs/).

Both blocks above are **abridged for reading**: the real code hoists every operand
through `ensure_utc(...)` into locals and wraps each comparison in `is not None` guards
(`:726-745`, `:753-783`), so a candidate with a null `valid_at` is *skipped*, never
compared — which is why an extraction backend that omits the optional dates (#1909)
disables invalidation entirely rather than degrading it. Comments inside the blocks are
mine; the statements are the repo's.

**(c) Where the dates come from.** `valid_at` / `invalid_at` are extracted by an LLM —
either inside the extraction call (`prompts/extract_edges.py::Edge`, fields'
descriptions at `:41-48`) or by a separate small-model call
`_extract_edge_timestamps` (`edge_operations.py:786-831`, response model
`EdgeTimestamps`, skipped if already set). `expired_at`/`created_at` are machine clocks.

**(d) A deterministic law corrects the LLM's dates.** `utils/maintenance/temporal_edge_utils.py`
is the closest thing in the repo to Strata's merge law. `_FUTURE_ASSERTION_RE`
(will/shall/plans to/…/'ll, `:19-30`), `_DEADLINE_RE` (`:32-34`), `_ONGOING_STATE_END_RE`
(`:36-41`); `normalize_future_temporal_bounds` (`:44-77`) clamps `valid_at` to the
reference time for future-tense assertions and **nulls `invalid_at`** when the fact is a
soft deadline rather than an ending. Comment in `edges.py:281-284` says the same: "soft
deadlines are not invalid_at". Regexes, not a model.

**(e) The candidate pool is the weak point, and it is still open in 0.31.0.**
Invalidation candidates come from a second hybrid search per edge with **no filter at
all** and the recipe default limit 10 (`edge_operations.py:558-568`):

```python
search(clients, extracted_edge.fact, group_ids=[extracted_edge.group_id],
       config=EDGE_HYBRID_SEARCH_RRF, search_filter=SearchFilters(), ...)
```

`DEFAULT_SEARCH_LIMIT = 10` (`search/search_config.py:29`). `add_triplet` does the same
(`graphiti.py:2123`). Confirmed still `SearchFilters()` at `main` @ `7f98907`.

Two open issues quantify the damage:

- **#1728 — collateral retirement.** "In one production graph of roughly 3,950 facts,
  1,616 (41%) carry an `invalid_at`. A hand-audit of four of them found three were
  collateral rather than genuine change" (https://github.com/getzep/graphiti/issues/1728).
  The killer edge shares *one* endpoint with its victim and carries a different relation
  name. Root cause named in the issue: PR #906 rerouted both searches through generic
  `search()`, the duplicate search kept its scoping (`edge_uuids` was added in the same
  PR), the invalidation search had no equivalent filter, and the old helper
  `get_edge_invalidation_candidates` (`search/search_utils.py:1587`) is now unreachable
  from ingestion — it survives only in tests — along with its `min_score=0.2`.
  **Independent reproduction on a different stack** (graphiti-core 0.29.3, Neo4j,
  `deepseek-v4-flash` judge, 109 episodes): 143/887 facts = **16.1%** carry
  `invalid_at`, concentrated by partition (49.2% in one 16-episode group), with 8 facts
  sharing one microsecond stamp — four *additive* facts about the same benchmark
  retired together. The `nanosecond == 0` vs microsecond split is a cheap audit
  instrument for separating model-authored from machine-written retirement dates.
  Fix PR #1729 (`_could_replace`: same pair either direction, or one shared endpoint
  **in the same position** plus a case-folded relation-name match) is **still open**.
  The thread also establishes the deeper gap: "Graphiti has no notion of cardinality
  today" — `Dana WORKS_AT Northwind → Contoso` should retire, `ServiceA USES Postgres`
  killing `ServiceB USES Postgres` should not, and no rule over endpoints and relation
  names separates them.
- **#1956 — retirement fails with history depth.** Hybrid search capped at 10 with no
  filter means a subject's dead history occupies the candidate slots, so the *live*
  fact is never shown to the judge. Measured on Neo4j 5.26.2 with a stub judge that
  marks every shown contradiction: "30/30 up to 9 past homes, 26/30 at 10, 14/30 at 20,
  **6/30 at 50**"; with a real model, "6/12 at 20 past homes, **0/12 at 50**. Every miss
  left two current homes." (https://github.com/getzep/graphiti/issues/1956). Fix PRs
  #1957 and #1970 are **still open**.
- **#1666 — the judge itself.** With a non-reasoning `small_model` (gpt-4.1-nano), the
  stock `EdgeDuplicate` schema scores **7/15**; declaring `reasoning: str` *before* the
  two arrays scores **14/15**; clear-contradiction 1/3 → 3/3
  (https://github.com/getzep/graphiti/issues/1666).
- **#1909** — with `structured_output_mode="json_schema"` on Ollama/llama.cpp, optional
  keys are omitted, so edges land with **no `valid_at`/`invalid_at` at all**
  (https://github.com/getzep/graphiti/issues/1909). Local-model Graphiti is a different
  system than API-model Graphiti.
- **#1645** — the read side: across 195 recorded searches, **30% of returned facts
  carried an `invalid_at`**, interleaved with live ones at equal rank
  (https://github.com/getzep/graphiti/issues/1645).

One design claim in the docs is **not in the code**: the 2024 blog says "We also
regenerate the fact stored on the invalidated edge to reflect updated knowledge"
(`Maria works as a junior manager` → `Maria used to work as a junior manager, until her
promotion…`). Grepping 0.31.0 finds no such rewrite — `.fact` is only ever written by
the save queries (`models/edges/edge_db_queries.py:93,158`). Treat narrative-rewriting
as Zep-product behaviour, not OSS Graphiti behaviour.

---

## Read path (hybrid search & rankers, LLM-free or not)

**The claim is true for the default path.** `Graphiti.search()`
(`graphiti.py:1903-1948`) uses `EDGE_HYBRID_SEARCH_RRF` — `bm25 + cosine_similarity`,
`reranker=rrf`, `limit=10` (`search_config_recipes.py:111-116`). The only model call is
one **embedding** of the query; `search/search.py` contains no `llm_client` reference at
all. An LLM enters the read path **only** if you choose
`EdgeReranker.cross_encoder`, or `center_node_uuid` (which switches to the
node-distance recipe, `graphiti.py:1945-1947`). Paper §3 frames it as
`f(α) = χ(ρ(φ(α)))` — search, rerank, construct — with no generation step.

**Search methods** (`search_config.py:28-48`): `cosine_similarity`, `bm25`,
`breadth_first_search` for edges and nodes; `bm25` only for episodes; `cosine`+`bm25`
for communities. Each configured method fetches **`2 * limit`** candidates in parallel
(`search.py:289-328`), so the default pool is ~20 per method. BFS with no origin nodes
runs "land and expand": the other results become the seeds (`search.py:341-357`),
`MAX_SEARCH_DEPTH = 3` (`search_utils.py:67`).

**Rankers — exactly five, with what each costs** (`search_config.py:53-70`;
dispatch at `search.py:366-462`):

| Reranker | Mechanism | Cost | Code |
| --- | --- | --- | --- |
| `rrf` | sum of `1/(rank + rank_const)` over result lists | in-process, O(candidates) | `search_utils.py:1775-1790` |
| `mmr` | λ·sim(q,d) − (1−λ)·max sim(d,d′) | **extra DB read of every candidate embedding**, then numpy | `search.py:384-402`, `search_utils.py:1901` |
| `cross_encoder` | rerank up to `2*limit` passages with a reranker model | 1 model call (LLM or local) | `search.py:404-417` |
| `node_distance` | rerank by hop distance to a centre node | 1 extra Cypher round trip; unreachable nodes score `inf` | `search_utils.py:1793-1853` |
| `episode_mentions` | rerank by count of `MENTIONS` edges (mention frequency) | 1 extra Cypher round trip | `search_utils.py:1855-1900` |

Two details that matter to us:

- **Graphiti's RRF uses `rank_const=1`, not 60** (`search_utils.py:1775`:
  `def rrf(results: list[list[str]], rank_const=1)`; score `1/(i + rank_const)`). Rank 1 is
  worth 1.0 and rank 2 worth 0.5 — far more top-heavy than Strata's k=60
  (`serve/memory.py:1043 rrf_k = 60.0`). Same algorithm, radically different sharpness.
- **Cross-encoder does not require an API.** `cross_encoder/bge_reranker_client.py:34-52`
  loads `BAAI/bge-reranker-v2-m3` via sentence-transformers and runs `predict` in a
  thread-pool executor — a local, CPU-runnable reranker. `OpenAIRerankerClient` and
  `GeminiRerankerClient` use log-probabilities of a boolean classification instead of
  generated scores.

**Filters** (`search/search_filters.py:27-73`): `node_labels`, `edge_types`,
`valid_at`/`invalid_at`/`created_at`/`expired_at` as lists of OR-lists of
`DateFilter{date, comparison_operator}` where the operator set includes `IS NULL` and
`IS NOT NULL` (`:27-35`, compiled at `:197-284`), plus `edge_uuids`,
`edge_source_node_uuids`, `edge_target_node_uuids` and generic `property_filters`
with `equals/not_equals/>/</>=/<=/IS NULL/IS NOT NULL`. **Default `search()` passes no
temporal filter**, which is exactly why retired facts are returned alongside live ones
(#1645). Getting "current facts only" means the caller writing
`invalid_at IS NULL` themselves — the recipe documented in issue #769 and repeated by
users (https://github.com/fabregog/graphiti-poc).

**Recipes**: 16 pre-built configs (`search_config_recipes.py`; enumerated in
https://blog.getzep.com/how-do-you-search-a-knowledge-graph/), e.g.
`COMBINED_HYBRID_SEARCH_RRF/MMR/CROSS_ENCODER`, `EDGE_HYBRID_SEARCH_NODE_DISTANCE`,
`NODE_HYBRID_SEARCH_EPISODE_MENTIONS`. This "pick a named recipe per call" surface is a
better shape than a single global ranking flag.

**Lookups that are not search**: `get_episodes_by_mentions(nodes/edges)`,
`get_mentioned_nodes` and `get_communities_by_nodes` (`search_utils.py:116-185`) walk
provenance with no embedding and no ranking — the blog calls these the zero-overhead
path when you already know the relevant messages. Note the blog also advertises the
forward direction, `graphiti.get_episode_mentions()`; **that method does not exist in
0.31.0 core** — a repo-wide grep finds only `get_episodes_by_mentions`.

**Latency numbers actually published:**

- Paper §4.3.2: end-to-end answer latency **3.20 s** (gpt-4o-mini) / **2.58 s** (gpt-4o),
  vs 31.3 s / 28.9 s full-context, at **1.6k** context tokens vs 115k. That is answer
  generation, not retrieval.
- Neo4j blog: "Zep's own Graphiti implementation achieves … P95 latency of 300 ms"
  (https://neo4j.com/blog/developer/graphiti-knowledge-graph-memory/).
- Zep marketing: "sub-200ms" — on the **proprietary** Context Graph Engine, not OSS
  (https://www.getzep.com/platform/context-graph-engine/).
- Independent, self-hosted on embedded Kuzu: **search p50 163 ms**, 212 memory tokens
  per query, but **ingest 3.65 s/turn** and **recall 37.0%**
  (https://github.com/hamza-dev-tech/agent-memory-bench).

The honest reading: retrieval really is LLM-free and fast; the cost has moved to the
write path, which is synchronous and LLM-bound.

---

## Organization: entities, communities, scopes

- **Entities carry evolving summaries.** `EntityNode.summary` (`nodes.py`, "regional
  summary of surrounding edges") + `name_embedding` + typed `attributes` + `labels`.
  Updated on write by appending edge facts (no LLM) or a batched LLM flight
  (`node_operations.py:1088-1170`), capped at `MAX_SUMMARY_CHARS = 1000`
  (`utils/text_utils.py:26`). Node search runs against **`name`**, not the summary
  (blog "How do you search a Knowledge Graph?").
- **Communities are label propagation, not Leiden.**
  `community_operations.py:96-140` implements iterative plurality-vote propagation with
  edge-count-weighted ballots. The paper says so explicitly: "we employ a label
  propagation algorithm rather than the Leiden algorithm", chosen because a single
  recursive step lets new nodes join an existing community without a full rebuild
  (§2.3). The **docs** say Leiden, which produced issue #358
  (https://github.com/getzep/graphiti/issues/358). Cost of `build_communities()`:
  `get_community_clusters` issues **one Cypher neighbour query per entity**
  (`community_operations.py:58-72`) — O(N) round trips — then
  `build_community` (`:201-250`) pairwise-summarises the cluster in a binary tree
  (O(|cluster|) LLM calls) and generates a name by LLM, which is then embedded and
  searched. Community summaries are built at write time so retrieval pays nothing
  (blog, same section).
- **Scopes.** `group_id` on every node and edge (`edges.py:51`, "partition of the
  graph") is the tenancy axis; searches take `group_ids` and `validate_group_ids`
  rejects empty/invalid ones. There is **no cross-group read by default** — issue #1919
  asks for an explicit `'*'` because "every search that omits group_ids returns 'No
  relevant facts found'" and the agent cannot distinguish that from an empty graph
  (https://github.com/getzep/graphiti/issues/1919).
- **Ontology is prescribed or learned.** Entity and edge types are Pydantic models;
  `edge_type_map: dict[(src_label, tgt_label), [edge_type_names]]` declares signatures
  and `enforce_edge_type_signatures` (`edge_operations.py:671-713`) **drops** a
  signature-violating edge under `strict_edge_types=True`, otherwise renames it to
  `RELATES_TO` and clears its attributes. Identity properties on a type double as the
  deterministic dedup key (`dedup_helpers.py:207-291`).
- **Sagas** (v0.29+, `nodes.py:907-921`) are the closest Graphiti object to Strata's
  journal: a `SagaNode` with `summary`, `first_episode_uuid`, `last_episode_uuid` and
  two watermarks — `last_summarized_at` (ingestion-time filter) and
  `last_summarized_episode_valid_at` (episode-time) — plus `summarize_saga()`
  (`graphiti.py:588-800`) that re-summarises only episodes newer than the watermark.
  Episodes chain via `NEXT_EPISODE`, sagas attach via `HAS_EPISODE`.
- **Hyper-edge-ish trick:** the same fact string may be extracted between several entity
  pairs, so one statement fans out to several edges (paper §2.2.2). Strata has no
  multi-subject fact at all.

---

## Backends & operational cost

`class GraphProvider(Enum)` (`driver/driver.py:59-63`) lists exactly four:
`NEO4J`, `FALKORDB`, `KUZU`, `NEPTUNE`.

| Backend | Requirement | Notes |
| --- | --- | --- |
| Neo4j | 5.26 | reference implementation; Lucene BM25 + vector + native traversal (blog, search article) |
| FalkorDB | 1.1.2 | Docker one-liner; `falkordblite` embedded needs Python ≥3.12; full-text is hand-rolled (`driver/falkordb/fulltext.py`) |
| Kuzu | 0.11.2 | **deprecated**, upstream unmaintained, emits `DeprecationWarning` (README) |
| Amazon Neptune | DB cluster or Analytics + OpenSearch Serverless | AOSS is the full-text backend; embeddings stored as comma-joined strings, similarity computed client-side (issue #1896) |

**There is no Postgres/pgvector backend in core.** Requests and attempts:
issue #779 (feature request), RFC #1781 (PostGraph via `post-graph`), PR #1777, and
issue #1896 asking for **NeuG** as an *embedded* backend — whose report of the status
quo is the useful part: "Neo4j and FalkorDB run a full `MATCH` and score cosine per
row; Neptune stores embeddings as strings and computes similarity in Python on the
client" (https://github.com/getzep/graphiti/issues/1896). A community driver exists
out-of-tree: https://github.com/uahic/graphiti-postgres.

Operational surface a Strata operator would object to:

- A separate database server **plus** an LLM with reliable structured output. The
  README warns: "Graphiti depends on structured (JSON) output for entity/edge
  extraction and deduplication, and works best with models … that reliably honor it …
  This is particularly problematic when using smaller models."
- Rate limits are a documented default: "concurrency is set low to avoid LLM Provider
  429 Rate Limit Errors."
- PostHog telemetry is **on by default**, opt-out via `GRAPHITI_TELEMETRY_ENABLED=false`
  (README "Telemetry"; code `graphiti_core/telemetry/telemetry.py`).
- `build_indices_and_constraints()` must be run before first ingest (`graphiti.py:803`).
- Every episode does ≥2 searches per extracted edge, then per-edge LLM calls, then
  per-node/edge saves — all network round trips to the graph DB.
- Community builds are O(N) round trips + O(cluster) LLM calls, and drift between full
  rebuilds (paper §2.3: "periodic community refreshes remain necessary").
- Practical write-ups: https://renezander.com/guides/graphiti-in-production/ and
  https://renezander.com/guides/graphiti-add-episode/.

---

## Evaluation & published numbers (claimed vs reproduced)

**Claimed, in the paper (arXiv:2501.13956, Jan 2025).**

DMR (Table 1): Zep **94.8%** (gpt-4-turbo) vs MemGPT 93.4%; but the paper's own
**full-conversation baseline is 94.4%** on the same model, and with gpt-4o-mini Zep
98.2% vs full-context 98.0%. The paper concedes DMR conversations are "60 messages,
easily fitting within current LLM context windows" and calls the benchmark inadequate.

LongMemEval_s (Tables 2–3, ~115k tokens average context, BGE-m3 embedder+reranker,
gpt-4o-mini / gpt-4o for construction and answering, top-20 edges + nodes retrieved):

| Memory | Model | Score | Latency | Context tokens |
| --- | --- | --- | --- | --- |
| Full-context | gpt-4o-mini | 55.4% | 31.3 s | 115k |
| Zep | gpt-4o-mini | **63.8%** | 3.20 s | 1.6k |
| Full-context | gpt-4o | 60.2% | 28.9 s | 115k |
| Zep | gpt-4o | **71.2%** | 2.58 s | 1.6k |

Per question type (gpt-4o-mini / gpt-4o): temporal-reasoning 36.5→54.1 / 45.1→62.4;
multi-session 40.6→47.4 / 44.3→57.9; single-session-preference 30.0→53.3 / 20.0→56.7;
**knowledge-update 76.9→74.4 (a regression) / 78.2→83.3**; single-session-assistant
**81.8→75.0 / 94.6→80.4 (a regression the paper flags as "a notable exception")**.

That knowledge-update regression on the weaker model is the same shape as Strata's
measured −0.139 turn_top1 drop on `knowledge-update` from the current rule
(`tools/lme_retrieval_ab.py`, A/B on 5,000 items) — evidence that update-style questions
are hard for *every* rewrite-the-past mechanism, not just ours.

**Reproduced / disputed.**

- **LoCoMo**: Zep claimed 84%; Mem0 re-ran it and reported **58.44% ± 0.20**, attributing
  the gap to including adversarial-category answers over a categories-1-4 denominator,
  prompt/template drift, and single runs; Zep then re-ran and published **75.14% ± 0.17**
  (https://github.com/getzep/zep-papers/issues/5, closed as completed 2025-05-19 with no
  further exchange). Three numbers for one benchmark, two of them vendor-run.
- **Current vendor figures** are self-reported with undisclosed configuration: LoCoMo
  94.7%, LongMemEval 90.2% (https://www.opensourceaireview.com/blog/which-ai-memory-layer-has-the-best-published-benchmarks-in-2026,
  https://atlan.com/know/zep-vs-mem0/).
- **Cognee** could not run Graphiti at all and used vendor-supplied numbers, and notes
  their own system was tuned while Graphiti was not
  (https://www.cognee.ai/blog/deep-dives/knowledge-graph-memory-benchmarks).
- **The only self-hosted reproduction found** grades retrieval rather than generation:
  Graphiti 0.30.2 on embedded Kuzu scored **recall 37.0%**, adversarial refusal 92.9%,
  search p50 163 ms, 212 memory tokens, ingest 3.65 s/turn
  (https://github.com/hamza-dev-tech/agent-memory-bench).
- **No agreed protocol exists.** Issue #1925 (Sep 2026) asks the maintainers to bless an
  open matched protocol — one LongMemEval release and one retrieval unit per system, same
  reader/prompt/top-k/temperature/judge, plus CPU/RSS/ingest/p50-p99 on the same machine
  (https://github.com/getzep/graphiti/issues/1925).

**Versus Strata.** Strata's LongMemEval oracle of **0.761** sits above Zep's published
71.2% (gpt-4o) — but the comparison is not apples-to-apples: Zep's number is an
end-to-end QA score with a specific reader/judge pair and a `s` variant of the dataset,
ours is an oracle measurement on a local 125B MoE with a pinned block. What *is*
comparable is the direction of the evidence: both systems beat a full-context baseline
in the 60s-70s band, and both regress on some question types.

---

## What Graphiti does better than Strata tier-3

1. **Two clocks instead of one.** Every fact separates world time (`valid_at`,
   `invalid_at`) from transaction time (`created_at`, `expired_at`)
   (`edges.py:271-289`), and the query API can filter on all four with `IS NULL`
   semantics (`search_filters.py:27-52`). Strata has only `created_at`/`updated_at`/
   `last_seen_at`/`invalid_at`, all transaction time (`serve/memory.py:328-339`,
   `:376-379`); `since`/`until` filter on `created` (`serve/memory.py:1021-1024`). So
   Strata can answer "when did we stop trusting this?" but not "was this true on 14
   March?" — and LME's temporal-reasoning category is exactly that question.

2. **Provenance is a traversable relation, not a column.** `EntityEdge.episodes` is a
   list of episode ids, materialised as `MENTIONS` edges, and both directions are
   queryable with zero embedding cost (`search_utils.py:116-185`; blog "Lookups").
   Strata keeps `provenance TEXT` as a JSON blob (`serve/memory.py:333`) and a separate
   `turns` table (`:340-348`) with no link from a fact back to the turns that produced
   it. Graphiti can show the sentence a fact came from; we cannot, without a manual join.

3. **A subject index with a maintained summary.** `EntityNode.summary` aggregates the
   edges around a node, refreshed incrementally on write, and node search is a first
   class retrieval scope alongside edges and communities (`search.py:473-673`). Strata
   has no subject index at all — the store is a bag of ~160-char rows, which is why 95%
   of rows sit on shelves too coarse to navigate and why right-fact-in-block decays
   ~3 points per doubling.

4. **A scoping primitive that shrinks the candidate pool for free.**
   `_pair_search_filter` (`edge_operations.py:399-412`) restricts a search to
   `src ∈ {a,b} AND tgt ∈ {a,b}` before ranking, and `edge_source/target_node_uuids`
   are exposed in the public filter API. Strata has shelves and scopes but no way to say
   "search only rows about *this subject*", so every near-dupe check costs a scope-wide
   cosine pass (`serve/memory.py:423`).

5. **Deterministic resolution that usually avoids the model.** Exact-name map →
   identity-property key → entropy-gated MinHash/LSH at 0.9 → only then the LLM
   (`dedup_helpers.py:321-390`, `:259-291`). Ambiguity escalates rather than merges
   (`:345-349`). Strata's guard is cosine-floor 0.85 + top-8 + the v3 law
   (`serve/memory.py:502-575`): the decision is deterministic, but the *proposal* step
   is a dense cosine scan with no inverted index, which is what made ingest quadratic
   (memory of the `_semantic_dupe` O(all-rows) rebuild).

6. **A pluggable, LLM-free-by-default read path with a real diversity objective.**
   Five named rankers, 16 recipes, per-call selection (`search_config.py`,
   `search_config_recipes.py`), including MMR (`search_utils.py:1901`) and a *local*
   cross-encoder (`bge_reranker_client.py:34-52`). Strata's blend is one fixed function
   (RRF k=60 + recency/confidence tie-breaks, `serve/memory.py:1029-1043`) and its
   read-path suppression is a hard threshold rather than a diversity trade-off.

7. **A global ("what is this store about") retrieval scope.** Communities
   (`community_operations.py`, `community_search` at `search.py:778-891`). Strata has no
   scope above a single row; a "summarise this project" question has no answer path.

8. **One row per real-world thing.** Entity resolution concentrates updates on a single
   node whose summary evolves; re-mentioning a thing extends its edges. Strata's
   curation loop re-derives the same policy in new words every sweep and produced
   **57 rows in two days** before the write guard existed (`serve/memory.py:439`).
   Graphiti's failure mode is different (it retires things it shouldn't) but its
   duplicate rate is structurally lower.

9. **Machine-checkable ontology.** Pydantic entity/edge types plus signature enforcement
   (`edge_operations.py:649-713`) and typed `attributes` with a cap-and-merge rule
   (`attribute_utils.py::apply_capped_attributes`). Strata's `class` is a free-text
   shelf mark produced by a keyword map with a 2-token margin and 18% "wild".

10. **Incremental narrative rollup with watermarks.** `SagaNode.last_summarized_at` /
    `last_summarized_episode_valid_at` and `summarize_saga()` (`graphiti.py:588-800`)
    re-summarise only the delta since the last run. Strata's compaction/consolidation
    has no equivalent watermark discipline.

11. **Everything is configurable per call and observable.** Per-prompt model routing
    (`llm_runtime`, `spec/llm-runtime.md`), prompt overrides, and an OTel span tree
    around every search phase (`search.py:81-96`, `OTEL_TRACING.md`). Strata's curation
    loop is one prompt and one timer.

---

## What Strata does that Graphiti does not

1. **No LLM in the interactive write path.** Curation runs on a ~30-min timer off the
   request; a write is an embed (~5 ms CPU) plus SQL (`serve/memory.py:419`, `:459-473`).
   Graphiti's `add_episode` is synchronous and needs ~2+2·E LLM calls per episode;
   independently measured at 3.65 s/turn.
2. **A deterministic merge law holds the final vote.** Subject veto → rare-token payload
   containment → Jaccard-0.75 drift → supersede-if-strictly-richer
   (`tools/m2_merge.py:118-197`, `serve/memory.py:515-575`), with the *survivor* rule
   (richer phrasing wins and the older row retires pointing at it) decided by token
   arithmetic. Graphiti's equivalent decision is delegated to a small model whose
   measured reliability is 7/15 on one schema variant (#1666) and whose *scope* is
   unbounded (#1728).
3. **Local-first, encrypted, single-file.** SQLite + FTS5 + vectors in one encrypted db,
   no server, no network, no third-party database. Graphiti cannot run without a graph
   DB (four backends, none embedded-and-off-the-shelf: Kuzu is deprecated, `falkordblite`
   needs Python ≥3.12, NeuG is still a feature request #1896).
4. **A context-assembly layer with a hard budget.** 96 notes / 24k chars, block pinned
   per conversation by an LRU keyed on the first user message
   (`serve/memory.py:16`, `:227-245`, `:306`, `:1125-1155`), plus eviction of the worst
   never-recalled rows when over cap (`:474-482`). Graphiti's OSS `search()` returns a
   ranked list; the constructor χ exists only in the paper, and context assembly is a
   Zep product feature.
5. **Read-path near-dupe suppression** before the block is rendered, at the same Jaccard
   bar the write guard uses. Graphiti has no read-time suppression at all: retired facts
   are returned interleaved at equal rank (issue #1645, 30% of 195 recorded searches).
6. **A measured scaling curve and honest negative results.** Right-fact-in-block −3
   points per store doubling, reranker +0.040, bigger budget zero gain, ~70% of residual
   errors are reasoning failures. Graphiti publishes no accuracy-vs-scale curve; the
   scale cliffs (#1728 at 3,950 facts, #1956 at ~10 past values) were found by users.
7. **A corpus-wide navigable taxonomy.** LCC-style shelf marks as a `class` column, with
   prefix-narrowed recall (`serve/memory.py:384-385`, `:840-841`). Graphiti's `labels`
   are per-instance types, not a call number; communities are the nearest analogue but
   are cluster summaries, not an address space.
8. **A pinned, hashed, CPU-only embedder with a byte-identical absent-config default**
   (bge-small-en-v1.5, 133 MB, SHA-256-pinned). Graphiti's default embedder is OpenAI;
   local embedders require hand-wiring an `OpenAIEmbedder` against Ollama/vLLM.
9. **Harness-independent injection plus an agentic tool plus a UI.** The block is
   assembled server-side so no memory text re-enters the window, `memory_search` is
   offered to the model, and a review list walks the version chain. Graphiti ships an
   MCP server and a FastAPI service but no assembly layer and no human review surface.
10. **Telemetry off by default** in practice (Strata makes no outbound calls); Graphiti
    phones home to PostHog unless you opt out.
11. **Deterministic, reproducible measurement discipline** — temp 0.8/1.0, ≥3 repeats,
    `X-Strata-Memory: off` to mute memory, `usage.prompt_tokens` asserted against the
    local render. Graphiti's own benchmark record is the opposite (single runs, disputed
    denominators, #1925 asking for a protocol).

---

## Portable ideas for Strata tier-3

Ranked by value per unit of risk. Everything here is SQL + Python; nothing needs a graph
database and nothing puts an LLM in the interactive path.

**1. Depth-aware supersede candidates — check whether we already have Graphiti's bug.**
*Mechanism*: Graphiti caps invalidation candidates at 10 with no filter, so a subject's
dead history evicts the live fact from the judge's view and it is never retired (#1956).
*Mapping*: our guard takes the cosine-sorted active rows in scope at ≥0.85 and tests
**at most the best 8** (`serve/memory.py:524`, `:552`). That is the same shape of
failure: with a deep near-duplicate family, the *currently live* predecessor can fall
outside the top 8 (or under the 0.85 floor) and never gets superseded, leaving two live
rows. *Cost*: one experiment, then possibly one line (raise the cap for rows that share
a rare subject token, which is a cheap inverted-index lookup). *How we measure it*: build
the #1956 test on our own store — N superseded variants of one fact + one new value,
count how often the live one retires, at N = 5/10/20/50, using `guard=True` and the real
bge-small vectors; report retirement rate vs N, 3 seeds. *Risk*: raising the cap re-opens
the quadratic write cost — that is exactly why the cap exists.

**2. A "could replace" structural gate before supersede/suppress.**
*Mechanism*: PR #1729's `_could_replace` — same pair either direction, or one shared
endpoint in the same position with a case-folded relation-name match — applied to the
candidate list *after* the model, bounding what a wrong judgement can destroy.
*Mapping*: we do not need it against a model, we need it against cosine. Before
`_semantic_dupe` returns `suppress`, require the pair to share the rare-token subject
(`df ≤ 3`, already computed as `u_new`/`u_ex`) **and** either identical payload head or
the same `class` prefix. We already have every input. *Cost*: ~25 lines in
`serve/memory.py:548-575` plus tests in `tools/test_m2_merge.py`. *How we measure it*:
replay both stores' dirty-write logs and count verdict changes; then the LME
knowledge-update arm of `tools/lme_retrieval_ab.py` (currently −0.139) at 3 repeats,
temp 0.8/1.0. *Risk*: too strict → stale facts linger; that is the safe direction and is
the explicit trade-off in #1728.

**3. As-of recall (`at=`) using the retirement chain.**
*Mechanism*: Graphiti's `DateFilter` + `ComparisonOperator` over four timestamps,
including `IS NULL` (`search_filters.py:27-52`). *Mapping*: `invalid_at is null` is
hardcoded at `serve/memory.py:654`; add `at: float | None` that compiles to
`created_at <= at AND (invalid_at IS NULL OR invalid_at > at)`, and walk `superseded_by`
for the version that was current then. *Cost*: ~20 lines + tests; no schema change,
because the columns already exist. *How we measure it*: build a temporal probe set from
our own version chains (rows with `superseded_by` not null), score
"what was true on date D" before and after; also run LME's temporal-reasoning category.
*Risk*: chains deeper than expected, or a chain whose head was itself evicted by the
budget. Both are detectable in the review list.

**4. Provenance edges: `turn_ids` on every fact.**
*Mechanism*: `EntityEdge.episodes` + `MENTIONS` + `get_episodes_by_mentions`
(`edges.py:267-270`, `search_utils.py:116-185`).
*Mapping*: add `turn_ids TEXT` (JSON array) filled by the curation loop from the journaled
turns it actually read; `source`/`provenance` already exist but point at nothing
addressable (`serve/memory.py:333`, `:340-348`). *Cost*: one `ALTER`, one field in the
curation output, one backfill tool. No graph DB — a JSON array plus `json_each()` is a
complete bidirectional index in SQLite. *How we measure it*: (a) an audit query for facts
whose evidence turns are gone or contradict them — Graphiti users found this worth an
issue thread; (b) does an "evidence" line in the injected block change answer accuracy;
(c) does evidence-scoped retirement reduce wrong retirements. *Risk*: turn pruning
invalidates ids; store turn ids as immutable.

**5. Sweep the RRF rank constant (k=60 vs k=1).**
*Mechanism*: Graphiti's `rrf(..., rank_const=1)` (`search_utils.py:1775`) is dramatically
sharper than our `rrf_k = 60.0` (`serve/memory.py:1043`). *Mapping*: it is one float.
*Cost*: one parameter plus a run; our own comment at `:1029-1042` already reasons about
the rank-step arithmetic, so the change is testable against a written prediction.
*How we measure it*: `tools/lme_retrieval_ab.py`, arms k ∈ {1, 10, 60}, turn_top1 and
block-hit per question type, 3 repeats, temp 0.8/1.0, memory muted via
`X-Strata-Memory: off` for the /v1 arm. *Risk*: k=1 lets one noisy list dominate; read
the per-type split, especially knowledge-update.

**6. Inverted index for the near-dupe proposal (MinHash/LSH or FTS5 rare-token query).**
*Mechanism*: `_build_candidate_indexes` keeps `lsh_buckets[(band_index, band)] → uuids`
so a fuzzy lookup touches only colliding buckets (`dedup_helpers.py:293-320`), plus an
exact-name dict. *Mapping*: our `_semantic_dupe` scans every active row in scope
(`serve/memory.py:423`, and `_scope_vecs`), which is the known quadratic cost. An FTS5
query over the rare subject tokens we already compute (`df ≤ 3`) gives the same
"only rows that could be the same thing" property using the index we already have.
*Cost*: ~40 lines; the law stays byte-identical, only the candidate set changes.
*How we measure it*: ingest wall-clock vs store size (fit the slope) at 2.5k / 10k / 50k
rows — `tools/m4_reach.py` and `tools/m4_embed.py` already drive this — and assert the
verdict set is unchanged on a replayed log (the way #1957's fix was validated: "on 2,000
random histories it changes no decision Graphiti makes"). *Risk*: a narrower candidate
set silently stops firing supersede across shelves.

**7. Subject rollup table — the cheap slice of "entities" that fixes shelf coarseness.**
*Mechanism*: `EntityNode.summary` maintained incrementally, searched as its own scope
(`nodes.py::EntityNode`, `node_operations.py:1088-1170`, `search.py:473-673`). *Mapping*: a
`subjects(normalized_subject, class, fact_ids, summary, updated_at)` table, where
`normalized_subject` is exactly the `df ≤ 3` rare-token subject the merge law already
extracts, maintained by the 30-min curation loop (not the write path). Recall then gains
a third list for RRF — subject-mediated — and the block can spend one slot on a subject
profile instead of five near-identical rows. *Cost*: one table + one loop step; no graph,
no per-write LLM. *How we measure it*: the doubling curve (right-fact-in-block at
2.5k / 10k / 50k) — if shelves are the problem, this is the fix that makes shelves
navigational; plus a "tell me about X" probe set; plus wild% falling as subjects get
assigned real shelf marks. *Risk*: subject extraction is noisy; a wrong subject groups
unrelated facts. Mitigate by keeping the raw rows in the block and the summary additive.

**8. Deterministic post-correction of curation-supplied dates.**
*Mechanism*: `normalize_future_temporal_bounds` — three regexes that clamp `valid_at` for
future-tense assertions and null `invalid_at` when the "end" is a soft deadline
(`temporal_edge_utils.py:19-77`), with the rule echoed in the field description
(`edges.py:281-284`) and in the prompt. *Mapping*: our curation loop emits
`invalid_at`; port the same guard before writing. *Cost*: ~40 lines, no model.
*How we measure it*: count live facts whose retirement date is a deadline rather than an
ending; adopt the #1728 audit trick — split our `invalid_at` values by sub-second
precision to separate model-authored from machine-written stamps, and hand-audit 10.
*Risk*: the regexes are English-only and tuned to their phrasing; ours should be
regression-tested against our own fact corpus.

**9. Retirement receipts.**
*Mechanism*: not in Graphiti's code — it is the fix proposed in #1728's thread
("preserve a reason/receipt for retirement (e.g. `same_pair`, `functional_replacement`,
`explicit_contradiction`) rather than only the final `invalid_at`", and the state
vocabulary active/contested/superseded/refuted/expired). *Mapping*: add
`retired_by_rule TEXT` next to `superseded_by`; our law already knows which of its four
rules fired. *Cost*: one column, one string. *How we measure it*: audit throughput —
minutes to classify a sample of retirements, before vs after; and whether the review
list can auto-sort the ambiguous ones. *Risk*: none material. This is the cheapest
item on the list and it makes #1728-class damage visible in our own store.

**10. MMR as an option on the read path, replacing hard suppression.**
*Mechanism*: `maximal_marginal_relevance(query_vector, candidates, λ)`
(`search_utils.py:1901-1940`), recipe `COMBINED_HYBRID_SEARCH_MMR` with `mmr_lambda=1`
(`search_config_recipes.py:53-77`). *Mapping*: candidates ≤240, embeddings already in
the db blob table; swap the Jaccard-0.75 suppression for a λ-tuned diversity pass over
the block. *Cost*: numpy over ≤240 rows, no model. *How we measure it*: LME full-set
against the 0.761 oracle, 3 repeats, plus a block-diversity proxy (distinct subjects per
block); split multi-session vs single-session, because MMR helps the former and can hurt
the latter. *Risk*: MMR can drop the second copy of a genuinely repeated fact, which is
what our suppression is deliberately doing.

**11. Make the reranker a per-call recipe, and keep it local.**
*Mechanism*: `EdgeReranker.cross_encoder` is one enum value on one call
(`search.py:404-417`), and `BGERerankerClient` runs `BAAI/bge-reranker-v2-m3` locally in
an executor thread (`bge_reranker_client.py:34-52`). *Mapping*: we measured +0.040;
expose it as an arm-level knob in `recall()` rather than a global flag, so A/B arms can
differ. *Cost*: VRAM/CPU contention with the 4090-resident MoE is the only real question.
*How we measure it*: p50/p95 added latency per recall call on the live box, and whether
+0.040 survives at 10k and 50k rows. *Risk*: the reranker steals GPU from the engine.

**12. Scope-reporting in errors.** Issue #1919: an empty result must say *which* scope was
searched, or the agent reads "nothing found" as "you don't know". One line in our
`memory_search` tool response. Cheap; do it whenever we touch that path.

### Explicitly not portable (ruthless list)

- **Entity/edge graph with pair-scoped candidate search.** The win is real but the price
  is Neo4j/FalkorDB plus a second persistence layer next to SQLite. Our analogue — a
  subject index (idea 7) — buys 70% of it for 5% of the cost.
- **Communities + label propagation.** `get_community_clusters` is one Cypher neighbour
  query per entity (`community_operations.py:58-72`) and `build_community` is an O(n)
  LLM pairwise reduce (`:201-250`). On one 24 GB GPU shared with a 125B MoE, that is a
  per-cycle LLM bill we explicitly refuse to pay, and the paper concedes communities
  drift between full rebuilds (§2.3).
- **`node_distance` / `episode_mentions` rerankers.** Both need hop traversal or an edge
  count query (`search_utils.py:1793-1900`). Our mention signal already exists as `hits`
  and `last_seen_at`; keeping them as tie-breakers (which we do, deliberately,
  `serve/memory.py:1036-1040`) is the right call.
- **Hyper-edge facts (one statement, many entity pairs)** and **typed Pydantic edge
  schemas**: both pay for structure we cannot validate without a model per write.
- **BFS "land and expand"** as a third retrieval list: needs adjacency traversal; our
  equivalent would be a subject-join, which idea 7 gives us anyway.

### The central question: where does each win at 10k–100k rows?

Graphiti's read path scales better in *latency* (index-backed BM25 + vector ANN,
163 ms p50 measured) and its scoping primitives keep candidate pools small. But its
**write-path accuracy degrades with history depth and group size, and it degrades
silently**: 41% of a 3,950-fact graph carrying `invalid_at` with 3 of 4 hand-audited
cases wrong (#1728), independent reproductions at 16.1%–49.2% by partition, and total
failure to retire the live fact past ~10 superseded values (#1956, 0/12 at 50). Nothing
in the system tells you either happened; the retired fact just stops being retrieved.

Strata's read path degrades measurably (−3 points per doubling in right-fact-in-block)
and its write guard costs O(active rows in scope) per dirty write. But its supersede
decision is deterministic, replayable, and *auditable*, and the merge law's subject veto
is precisely the guard Graphiti's `_could_replace` is trying to invent — with the
important difference that Graphiti's guard compares **relation names** (which an LLM
writes inconsistently: `Uses` and `USES` coexist in the audited graph) while ours
compares **rare content tokens** (which are in the text).

At 100k rows, Graphiti needs a graph database and an LLM budget per write; Strata needs
an inverted index for the proposal step and a subject layer to stop the block from
filling with variants. Both are one-time engineering, not a new dependency. The cheapest
slice of Graphiti worth taking is therefore **not the graph**: it is (1) depth-aware
candidate selection, (2) the structural gate on retirement, (3) two timestamps and an
as-of read, and (4) provenance ids — all four are columns and predicates in the SQLite we
already run.

---

## Search log

All queries issued through the Brave Search MCP (`brave_web_search` unless marked
`llm_context`, which returns page content rather than links). 28 queries + 7 direct page
fetches, 2026-10-14.

1. `Graphiti temporal knowledge graph incremental construction episodes entities edges`
2. `Graphiti temporal edge invalidation invalid_at expired_at LLM`
3. `Zep Graphiti retrieval no LLM calls latency milliseconds`
4. `Graphiti hybrid search BM25 cosine graph distance RRF MMR reranker`
5. `Graphiti communities Leiden algorithm group_node_ops summary retrieval`
6. `Graphiti getzep temporal knowledge graph invalidation invalid_at expired_at`
7. `Graphiti hybrid search rerankers MMR RRF graph distance` *(llm_context)*
8. `Graphiti getzep temporal knowledge graph incremental construction episodes entities edges documentation`
9. `Graphiti edge invalidation invalid_at expired_at how decided LLM`
10. `Zep Graphiti retrieval sub-second latency no LLM summarization milliseconds`
11. `Graphiti hybrid search BM25 cosine breadth-first-search reranker node_distance episode_mentions`
12. `Graphiti communities Leiden algorithm community summary group_id`
13. `Graphiti backends Neo4j FalkorDB Amazon Neptune Kuzu Postgres pgvector`
14. `Zep temporal knowledge graph architecture paper LongMemEval 95% accuracy 90% latency arxiv`
15. `Graphiti DMR benchmark 94.8% Zep deep memory retrieval`
16. `getzep graphiti issue 1728 edge invalidation searches whole graph unrelated facts retire`
17. `graphiti issue 1956 edge invalidation misses current fact superseded facts scale`
18. `Graphiti add_episode how many LLM calls per episode token cost`
19. `Zep LongMemEval reproduction independent benchmark criticism graphiti`
20. `Graphiti LOCOMO benchmark score comparison Mem0 Cognee`
21. `Graphiti hacker news reddit is it worth it criticism overhead`
22. `Graphiti saga node concept episodes incremental summary`
23. `Graphiti MCP server search_memory_facts tool invalid_at stale facts`
24. `Graphiti production latency p95 ingest seconds per episode benchmark self-hosted`
25. `Zep documentation hybrid search rerankers latency sub-200ms context graph`
26. `Graphiti Postgres pgvector backend without graph database issue`
27. `getzep graphiti issue 1666 contradiction detection small model reliability` *(llm_context)*
28. `getzep graphiti issue 1925 open matched memory benchmark protocol` *(llm_context)*

Direct page fetches: https://github.com/getzep/graphiti (README) ·
https://arxiv.org/html/2501.13956v1 (full paper incl. Tables 1-3 and appendix prompts) ·
https://www.getzep.com/blog/beyond-static-knowledge-graphs/ ·
https://blog.getzep.com/how-do-you-search-a-knowledge-graph/ ·
https://github.com/getzep/graphiti/issues/1728 ·
https://github.com/getzep/graphiti/issues/1956 ·
https://github.com/getzep/zep-papers/issues/5

Code read (clone @ `7f98907`): `graphiti.py` (`add_episode`, `add_triplet`, `search`,
`summarize_saga`), `nodes.py`, `edges.py`, `utils/maintenance/{edge_operations,
node_operations, dedup_helpers, community_operations, temporal_edge_utils,
combined_extraction, attribute_utils}.py`, `utils/{content_chunking,text_utils}.py`,
`search/{search,search_config,search_config_recipes,search_filters,search_utils}.py`,
`prompts/{extract_edges,dedupe_edges,dedupe_nodes}.py`,
`cross_encoder/{bge,openai,gemini}_reranker_client.py`, `driver/driver.py`,
`driver/*/operations/search_ops.py`, `helpers.py`, `spec/*`,
`tests/utils/maintenance/test_edge_operations.py`, `pyproject.toml`.

---

## Unverified claims

1. **`add_episode` LLM-call count** (`2 + 2·E + ⌈N/30⌉`) is derived by reading the call
   sites in `graphiti.py:1440-1530` and `edge_operations.py`, not measured. I did not run
   Graphiti. Treat it as an upper bound with per-edge attribute extraction as the swing
   factor; `combined_extraction.py` / `add_episode_bulk` may collapse the two extraction
   calls into one.
2. **Paper numbers are from Jan 2025 against v0.x Graphiti.** The current engine
   (0.31.0) has a different write path (combined extraction, saga nodes, decision-model
   dedupe, GLiNER2). Nobody has republished LongMemEval on 0.31.x. The 63.8%/71.2%
   figures should be labelled "Zep paper, 2025", not "Graphiti today".
3. **`agent-memory-bench` (recall 37.0%, ingest 3.65 s/turn) is a single third-party run
   on embedded Kuzu with an unstated extraction model.** One repo, no repeats published,
   Kuzu is a deprecated backend. It is the only self-hosted, retrieval-graded number I
   could find — do not treat it as authoritative, and it is not comparable to our LME
   oracle.
4. **"No fact regeneration on invalidated edges" in 0.31.0** is a negative grep result
   (`grep -rn "\.fact = |used to|regenerat"` over `graphiti_core/`). Zep's hosted product
   may still do the narrative rewrite the 2024 blog describes; I could not check the
   closed-source side.
5. **Docs-vs-code drift, both directions.** Communities: docs say Leiden, code and paper
   say label propagation (#358). `SEMAPHORE_LIMIT`: README says 10, `helpers.py:46` says
   20. Paper §2.2.2 says edge dedup is "constrained to edges existing between the same
   entity pairs" — true for the duplicate pool, false for the invalidation pool. I read
   the code, so I trust the code, but I did not run either path to confirm which one a
   default install actually takes.
6. **Cognee's Graphiti results were supplied by Graphiti**, not run by Cognee
   (https://www.cognee.ai/blog/deep-dives/knowledge-graph-memory-benchmarks), and they
   disclose that Cognee was tuned and Graphiti was not. Not usable as an independent
   number.
7. **Issue-quoted line numbers** (e.g. `edge_operations.py:407-417` in #1956, `:399/414`
   in #1728) are from 0.29.3/0.30.2 forks and do not match the 0.31.0 clone. The *code
   behaviour* they describe does still hold in the clone — I verified `SearchFilters()`
   at `edge_operations.py:565` and `graphiti.py:2123`.
8. **Strata-vs-Graphiti LME comparison (0.761 vs 0.712) is not controlled.** Different
   reader models, judges, dataset variants (`LongMemEval_s`), and our figure is an oracle
   measurement. Directionally interesting, numerically not a claim.
9. **Neptune's client-side cosine** and **FalkorDB's hand-rolled full-text** are asserted
   by issue #1896 and inferred from `driver/falkordb/fulltext.py`; I did not measure
   either backend.
10. **`hamza-dev-tech/agent-memory-bench`, `4ktLuffy/graphiti-temporal`,
    `Deepnar/ice`, `uahic/graphiti-postgres`, `Archolith/graphiti`** are third-party
    forks/harnesses I read only through search snippets and the issue threads; I did not
    audit their code or their ground truth.
