# Zep (getzep/zep + Zep Cloud + the paper) — deep read for Strata tier-3

Researched 2026-10-14. Code read from two shallow clones:

* `https://github.com/getzep/zep` @ `3997767fccf83bbea7b54ad5f73554f2e5bcd54e`
  ("Migrate the framework integrations to the Zep v4 SDK (#631)", 2026-10-14) → `/tmp/opencode/research/zep`
* `https://github.com/getzep/graphiti` @ `7f989075b2eae0ad4db938e4f703f3b0701d73f3`
  ("chore: set graphiti-core version to 0.31.0 for the v0.31.0 release", 2026-10-14) → `/tmp/opencode/research/graphiti`

Paper: https://arxiv.org/abs/2501.13956 (v1, 20 Jan 2025, CC BY-NC-SA 4.0, 12 pages, 3 tables)
· PDF mirror: https://blog.getzep.com/content/files/2025/01/ZEP__USING_KNOWLEDGE_GRAPHS_TO_POWER_LLM_AGENT_MEMORY_2025011700.pdf
· Docs (v4): https://help.getzep.com/v4/llms.txt · Benchmarks page: https://www.getzep.com/research/

Line numbers are exact for the clones above. Docs quotes are from the v4 (default) pages fetched
2026-10-14. The companion spike `spikes/memory_systems/graphiti.md` (same Graphiti commit) covers the
engine in more depth; this file covers **Zep** — the paper, the managed product, the deprecated
Community Edition, the numbers and their contestation — and the Zep↔Graphiti boundary. Where both
files touch the same mechanism, this one quotes it briefly and defers.

---

## What it is

**Three different things share the name "Zep". Getting this boundary right is the single most
important thing in this spike, because every published number attaches to a different one.**

1. **Zep Cloud / Zep today** — a commercial, managed "unified context layer". Its own docs say it
   plainly: *"Zep is agent memory at enterprise scale. It runs Graphiti inside a managed system —
   extraction, retrieval, storage, and governance on the proprietary Context Graph Engine — and
   serves millions of governed Context Graphs as one Context Lake. In short: Graphiti builds the
   graph; Zep operates it."* — https://help.getzep.com/v4/zep-vs-graphiti.md. Storage is
   **Konig / the Context Graph Engine**, a proprietary graph database service, not Neo4j
   (https://www.getzep.com/platform/graphiti/, https://www.getzep.com/platform/context-graph-engine/).
   The 2026 headline numbers (LoCoMo 94.7%, LongMemEval 90.2%) are measured on **this** product.
2. **`getzep/zep` the GitHub repo** — *not* the product. The README's first substantive line:
   *"This repository is **not** Zep's product or service. It contains **example code, framework
   integrations, and tools**"* (`zep/README.md:19-21`). Contents are `examples/`, `integrations/`,
   `ingestion/` (`zep-ingest`), `ontology/`, `benchmarks/`, `zep-eval-harness/`, `mcp/`, and
   `legacy/` (`zep/README.md:33-45`).
3. **Zep Community Edition (CE)** — the old self-hostable server, now dead: *"Zep Community Edition
   is no longer supported. Its code has been moved to the `legacy/` folder"*
   (`zep/README.md:66-70`, announcing post
   https://blog.getzep.com/announcing-a-new-direction-for-zeps-open-source-strategy/). CE was a Go
   HTTP API + Postgres that **delegated 100% of memory work to a Python Graphiti sidecar over HTTP**
   (see "Write path" and "Read path" below).

**And the paper's architecture is Graphiti, not the repo.** The paper says so: *"we introduce Zep, a
memory layer service powered by Graphiti"* and cites Graphiti as reference [6]
(https://arxiv.org/abs/2501.13956 §1). Meanwhile the paper's numbers were produced by **Zep's hosted
service**: *"We performed testing using a consumer laptop from a residential location in Boston,
connecting to Zep's service hosted in AWS us-west-2"* (§4.3). So:

> The paper describes Graphiti (OSS, Apache-2.0) but measures Zep Cloud (closed, managed). Neither
> `pip install graphiti-core` nor the retired CE reproduces 71.2% out of the box. Zep's own
> Zep-vs-Graphiti table lists "proprietary extraction LLMs, reranker, and embedding models" and
> "Observations, graph analysis" as things Zep adds on top of Graphiti
> (https://help.getzep.com/v4/zep-vs-graphiti.md).

**Data model (both Zep and Graphiti).** Three tiers — episode subgraph, semantic entity subgraph,
community subgraph — with formal incidence `φ: E → N×N` (paper §2). In the current product there are
**six retrievable context types**: facts (edges), entities (nodes with rolling summaries), episodes
(raw), observations (derived cross-entity patterns), thread summaries, and the user summary
(https://help.getzep.com/v4/context-types.md, https://help.getzep.com/v4/observations.md). Facts
live *on edges* as an NL sentence plus a `name` relation type in `SCREAMING_SNAKE_CASE`
(`WORKS_AT`, `LIVES_IN`) — https://help.getzep.com/v4/facts.md, "Edge names".

**Strata comparison.** Strata tier-3 is a flat table of ~160-char facts with `scope` + `class` +
validity columns. Zep is a graph where the fact is an edge payload between two resolved entities,
plus four derived tiers. The interesting part for us is not the graph — it is *what Zep puts on the
read path and in the block* (validity ranges, multiple granularities, character-budget packing) and
*what Zep refuses to do at read time* (no LLM summarization in retrieval).

---

## Write path

### Zep Cloud (v4, current)

Documented six-step pipeline, one episode at a time, **each step an LLM pass**, guided by a
per-graph ontology and custom instructions
(https://help.getzep.com/v4/how-graph-creation-works.md):

| Step | What happens |
|---|---|
| 1. Extract entities | candidate entities in the episode |
| 2. Extract relationships / facts | relationships + the facts describing them |
| 3. Date facts | *a separate model pass* assigns `valid_at`/`invalid_at` using the episode's event time as reference |
| 4. Resolve entities | match against existing nodes; unresolved mentions become new nodes |
| 5. Resolve relationships / facts | duplicates merge; **contradictions invalidate older facts** |
| 6. Summarize entities | node summaries updated from new evidence |

Then embed and persist episode + nodes + edges.

Two documented properties of this pipeline matter enormously for us:

* **Merge policy is deliberately biased to under-merge.** *"When identity is unclear, Zep prefers
  **under-merge** over **over-merge** … duplicate nodes are usually recoverable — high-recall
  retrieval often still surfaces both `Sarah` and `Sarah Brown` for the same query, so incomplete
  identity resolution often does not break the agent path."*
  (https://help.getzep.com/v4/how-graph-creation-works.md, "How entity resolution works").
* **Ingestion is asynchronous and slow relative to the request.** *"Zep ingestion can take a few
  minutes. The Context Block can omit information from recent messages during that time"* — so the
  app is told to *"Provide the last four to six messages"* alongside the block
  (https://help.getzep.com/v4/retrieving-context.md). There is a dedicated
  "Check Data Ingestion Status" page and a Batch API
  (https://help.getzep.com/v4/check-data-ingestion-status.md,
  https://help.getzep.com/v4/adding-batch-data.md).

Hard limits worth knowing: an episode body is capped at **10,000 characters**
(`zep/ingestion/README.md`: *"a 10,000-character episode limit, DIY chunking, timestamps that
silently default to ingestion time (corrupting fact timelines), entity aliases that never merge"*).
That last clause is a warning Strata should respect: if the client doesn't send an event timestamp,
the timeline silently becomes ingest time.

### Graphiti OSS (the engine the paper describes) — call count per write

`add_episode` (`graphiti/graphiti_core/graphiti.py:1332`) runs, per episode:
`retrieve_episodes` (context) → `extract_nodes` → `resolve_extracted_nodes` →
`_extract_and_resolve_edges` → `extract_attributes_from_nodes` → `_process_episode_data`
(→ optional communities). Inside those:

* `generate_prompt_response` appears **5×** in `utils/maintenance/node_operations.py`,
  **6×** in `utils/maintenance/edge_operations.py`, **3×** in `community_operations.py`,
  **2×** in `graphiti.py`.
* Per **edge**: one `dedupe_edges.resolve_edge` call (duplicate + contradiction indices), one
  timestamp call (`_extract_edge_timestamps`, `edge_operations.py:786-831`) when the extractor
  didn't already date it, and an optional attribute-extraction call (`edge_operations.py:1039-1066`).
* Per **node**: a dedupe call, only *after* deterministic gates fail (see below).
* Fast paths that skip the LLM entirely: exact normalized fact + same endpoints
  (`edge_operations.py:940-951`), and no candidates at all (`edge_operations.py:905-938`).

Real-world cost, measured by someone running it: *"Graph extraction is the largest share of
knowledge-ingest's LLM spend: about **28 LLM calls per 30k-character part**, at about **8.7k input
tokens per call**"* — https://github.com/GetKlai/klai/pull/1745. Wall-clock: *"My first ingest
script awaited one episode at a time: about **2 minutes per item**, close to 7 hours for 200"*
— https://renezander.com/guides/graphiti-add-episode/. Throughput at default concurrency:
*"about **50 episodes per minute**"* — https://pub.towardsai.net/i-needed-memory-for-my-ai-agent-and-found-this-knowledge-graph-framework-55c94880820e.
Graphiti's own README warns that concurrency is deliberately low: `SEMAPHORE_LIMIT` defaults to
`10` *"to avoid LLM Provider 429 Rate Limit Errors"* (`graphiti/README.md`).

**There is a bulk path**, and it is the closest thing in Zep's world to Strata's timer:
`add_episode_bulk` (`graphiti.py:1586`) with `use_combined_extraction=True` → *"one combined
node-and-edge LLM call per episode"* instead of two. Its docstring states that *"Edge invalidation
and date extraction (`valid_at` / `invalid_at`) are performed in the bulk path as well"*. But read
`utils/bulk_utils.py:504-600` and the picture is thinner: candidate finding is done **in memory**
with *"Approximate BM25 by checking for word overlaps (this is faster than creating many in-memory
indices)"* plus cosine ≥ `min_score = 0.6`, restricted to identical `(source, target)` pairs, and
the code then says *"For now we won't track edge invalidation"* (`bulk_utils.py:578`). Node-side bulk
dedup adds a second
deterministic pass over the batch union, with an admitted `O(n²)` note
(`bulk_utils.py:431-436`). **Read: the OSS bulk path is a backfill accelerator, not a
curation-loop equivalent.**

### Zep Community Edition (retired) — what a "self-hosted Zep" actually was

* Go server (`legacy/src/api/`), Postgres/`ankane/pgvector` for users/sessions/**messages**, and a
  `zepai/graphiti:0.3` container talking to **Neo4j 5.22** — four services in
  `legacy/docker-compose.ce.yaml`; Graphiti is reached at `graphiti.service_url: http://graphiti:8003`
  (`legacy/zep.yaml:28-30`).
* Write: `PutMemory` posts the messages to Graphiti **twice** — once for the session group, once for
  the user group (`legacy/src/store/memory_ce.go:62-72`).
* The Go layer held **no** extraction logic at all. `legacy/src/lib/graphiti/service_ce.go` is a
  plain HTTP client (`GetMemory`, `PutMemory`, `Search`, `AddNode`, `GetFact`, `DeleteFact`,
  `DeleteGroup`, `DeleteMessage`).
* `legacy/src/lib/search/rrf.go` and `mmr.go` exist but have **no call sites anywhere in the CE
  tree** (grep over `legacy/src` returns only their own definitions) — dead code from an earlier
  architecture. All retrieval was Graphiti's.

**Strata comparison.** Zep's write path is *LLM-heavy and asynchronous*: ~6 passes per episode,
minutes of lag, an explicit "recent messages may be missing" caveat, and a documented merge bias
toward under-merge. Strata's is *LLM-free in the hot path*, batched on a 30-min systemd timer, with a
deterministic merge law. The honest framing: **Zep pays per-write compute to make reads cheap and
deterministic; Strata pays per-read complexity to make writes free.** Both end up with a
deterministic retirement decision — see the key section.

---

## Read path

### Zep Cloud: three layers of choice

1. **Auto search / Smart Context Assembly** — `graph.get_context(graph_uuid, query, max_characters)`;
   default `max_characters` **2,500**, cap **50,000**; returns one assembled string plus optional
   typed results with a `selection_rank`
   (https://help.getzep.com/v4/searching-the-graph.md, "Auto Search"). It *"retrieves across all of
   them in parallel, applies a cross-scope rerank, and dynamically composes the most relevant
   results"*. It **has no `reranker` parameter** — the cross-scope rerank is internal. Auto search
   *"does not select episodes, so `episodes` is always empty"*.
   The announcement quantifies the trade: **86.5% at a median 2,680 tokens vs 94.7% at 5,760** —
   *"54% fewer tokens for about 8 points of accuracy"*
   (https://www.getzep.com/blog/smart-context-assembly-fewer-tokens-higher-quality/).
2. **Five scoped searches** — `graph.search_edges|nodes|episodes|observations|thread_summaries`,
   each `page_size` ≤ 50, each accepting a reranker subset.
3. **Filters** — entity/edge type, exclusion, node/episode sets, property filters, metadata filters,
   and **datetime filters over all four timestamps** (edge scope only).

**Retrieval primitives** (identical in OSS and product): cosine similarity, Okapi BM25 full-text
(Neo4j's Lucene), and breadth-first search over the graph, `bfs_origin_node_uuids` accepting **up to
five** node/episode UUIDs — the documented pattern is to seed BFS with the newest 5 episodes of the
current thread to pull in recently-mentioned entities
(https://help.getzep.com/v4/searching-the-graph.md, "Breadth-first search"; paper §3.1).

**Rerankers** (`graphiti_core/search/search_config.py:57-77`; documented at
https://help.getzep.com/v4/searching-the-graph.md#rerankers):
`rrf` (default), `mmr`, `cross_encoder`, `node_distance`, `episode_mentions`.
Implementations in `graphiti_core/search/search_utils.py`:

```python
def rrf(results: list[list[str]], rank_const=1, min_score: float = 0) -> ...:
    for i, uuid in enumerate(result):
        scores[uuid] += 1 / (i + rank_const)        # search_utils.py:1776-1781
```

* **Zep's RRF constant is 1, not 60.** `rank_const=1` ⇒ rank-1 contributes `1/1`, rank-2 `1/2`:
  a *much* steeper fusion than the Cormack-Clarke `k=60` Strata uses. The retired CE used the same
  shape: `rankings[uuid] += 1.0 / float64(rank+1)` (`zep/legacy/src/lib/search/rrf.go:15-21`).
* **MMR defaults** `DEFAULT_MMR_LAMBDA = 0.5`, `DEFAULT_MIN_SCORE = 0.6`, `MAX_SEARCH_DEPTH = 3`
  (`search_utils.py:65-67`). Note a trap: `COMBINED_HYBRID_SEARCH_MMR` sets `mmr_lambda=1`
  (`search_config_recipes.py:52-56`), and at λ=1 the implemented score
  `mmr_lambda*sim + (mmr_lambda-1)*max_sim` (`search_utils.py:1929`) has **zero diversity term** —
  that recipe is pure relevance.
* `episode_mentions` = frequency-of-mention reranking; `node_distance` = graph-distance from a
  centroid node (`search_utils.py:1793+`). The paper calls these out as Zep-specific: a
  *"graph-based episode-mentions reranker … enabling a system where frequently referenced
  information becomes more readily accessible"* and a *"node distance reranker … providing context
  localized to specific areas of the knowledge graph"* (§3.2).
* Cross-encoder clients: `graphiti_core/cross_encoder/` (bge, openai, gemini). Zep Cloud documents a
  sigmoid `[0,1]` score, a rank-aligned `relevance` field, and `filter_low_quality_results` which
  cuts *"at the knee of the cross-encoder score curve, and keeps at least 5 results"*.
* **Graceful degradation:** *"If the graph embedder is unavailable, search continues with BM25
  full-text search in that graph's model tables. The vector leg is omitted. Recall can be lower."*
  (https://help.getzep.com/v4/searching-the-graph.md).

**Auto search has a temporal query classifier.** *"For supported relative requests, Zep uses a
machine learning model to determine whether the phrase expresses a temporal retrieval constraint …
When it does, Zep resolves the corresponding calendar window and restricts the retrieved facts
automatically"*; the resolved window and timezone are then stated inside the returned block. If
confidence is low, no window is applied. Applies **only** to `graph.get_context`
(https://help.getzep.com/v4/searching-the-graph.md#relative-time-in-auto-search).

**Explicit anti-LLM stance at read time.** *"The Context Block is shaped at retrieval, not generated
by an LLM"* (https://www.getzep.com/research/); *"Hybrid Retrieval … without reliance on LLM
summarization"* (`graphiti/README.md`); Graphiti-vs-GraphRAG table lists retrieval latency
*"sub-second"* vs GraphRAG *"seconds to tens of seconds"* (`graphiti/README.md`).
*"Zep uses one-shot retrieval … no 'retrieve, realize we need more, retrieve again' pipeline"*
(https://www.getzep.com/blog/the-retrieval-tradeoff-what-50-experiments-taught-us-about-context-engineering/).

**Retrieval philosophy — and where it disagrees with our measurements.** Zep says *"We optimize for
recall over precision … Modern LLMs are good at filtering irrelevant context in their input. They're
much less good at inferring facts that aren't there"* and sets product defaults near 15 edges / 5
nodes. Their own sweep (below) shows the curve **flattens but does not invert** on LoCoMo:
more context never hurt. Strata measured the opposite shape at the *store-size* axis (right-fact-in-block
falls ~3 points per doubling) and zero gain from a bigger budget. These are different axes; see
"Portable ideas" #3.

**Strata comparison.** Strata: candidates → blend (rank/recency/confidence) → read-path near-dupe
suppression → 96-note / 24k-char block pinned per conversation, plus an agentic `memory_search` tool.
Zep: candidates (3 signals) → one reranker → **character-budget** packing across **six** shapes →
block, plus scoped tools for agents. Two concrete deltas: (a) Zep **never suppresses near-duplicates
at read time** — that is Strata's read-path win; (b) Zep **packs to characters, not counts**, and
lets the shape vary per query — that is Zep's win, and it is measurable on our harness.

---

## Organization & scoping

**Zep Cloud (v4).** Scoping is by **graph UUID**, and there are two kinds
(https://help.getzep.com/v4/searching-the-graph.md,
https://help.getzep.com/v4/how-to-share-context-across-users-using-graphs.md,
https://help.getzep.com/v4/architecture-patterns.md):

* **User graph** — one per Zep user; the user UUID *is* the graph UUID. Threads attach messages to a
  user; `thread.get_context(thread_uuid)` searches the **whole user graph**, so a block may mix the
  current thread, other threads, and business data
  (https://help.getzep.com/v4/retrieving-context.md).
* **Shared Context Graph** — a project / product / customer-account / domain graph, created by
  `graph.create`, searched by its UUID, and authorized separately
  (*"Apply application authorization and Zep policies before you search a shared graph"*).
* **Graph directory** — discover shared graphs by name/description *before* searching contents
  (https://help.getzep.com/v4/graph-directory.md). This is a coarse **router over graphs**, the
  closest thing Zep has to a query→shelf router — but it routes between whole graphs, not between
  categories inside a graph.
* Governance on top: RBAC, ABAC policy sets on API keys and UserGroups, source traceability, content
  policies, audit + API logs (https://help.getzep.com/v4/governance.md,
  https://help.getzep.com/v4/memory-security.md).

**Graphiti OSS.** Scoping is a single string: `group_ids` (namespacing —
https://help.getzep.com/v4/graphiti/core-concepts/graph-namespacing.md). Two operational bugs found
in the wild are worth noting for anyone tempted to treat `group_id` as a security boundary:
concurrent multi-`group_id` ingestion corrupts placement (*"we audited FalkorDB after normal
concurrent usage and found 19 misplaced episodes spread across 5 different graphs"* —
https://github.com/getzep/graphiti/issues/1676), and `group_id` selects a database for writes but
not for reads (*"reads come back empty with no error"* —
https://github.com/getzep/graphiti/issues/1876).

**Typing.** Zep's ontology feature = Pydantic models for entity and edge types, set per graph
(`graph.set_ontology`) or project-wide, plus `identity_properties` for hard identity keys
(https://help.getzep.com/v4/customizing-graph-structure.md,
https://help.getzep.com/v4/prepare-data-for-ingestion.md). The repo ships a default ontology where
classification is prompt-engineered with explicit priority rules — e.g. `Preference`: *"IMPORTANT:
Prioritize this classification over ALL other classifications except User and Assistant … Use LOW
THRESHOLD"* (`zep/ontology/default_ontology.py`). Graphiti enforces type signatures mechanically:
`enforce_edge_type_signatures` drops or renames an edge whose type doesn't match its endpoint labels
(`edge_operations.py:649-712`).

**Strata comparison.** `scope(user/project)` ≈ Zep's user graph vs shared Context Graph — the same
two-axis split, arrived at independently. Strata's `class` (17 LCC shelf marks + 18% wild) has **no
Zep equivalent as a retrieval partition**: Zep's `class`-analogue is *entity/edge labels*, which are
a **filter** the caller supplies, not an auto-routed partition, and Zep equally lacks a query→label
router. Where Zep is strictly ahead is **granularity tiers** (facts / entity summaries / observations
/ thread summaries / user summary) and **typed identity properties**. Where Strata is ahead:
per-row confidence and a deterministic shelf assignment that costs nothing.

---

## Temporal validity & invalidation (the key section)

### The four timestamps

Zep's bi-temporal claim, as documented today (https://help.getzep.com/v4/facts.md, "The four fact
timestamps", and https://www.getzep.com/blog/beyond-static-knowledge-graphs/):

| Field | Axis | Meaning |
|---|---|---|
| `created_at` | transaction (`T'`) | the time Zep learned the user got married — **always set** |
| `expired_at` | transaction (`T'`) | the time Zep learned the fact was false — nullable |
| `valid_at` | world (`T`) | the time the fact became true — nullable, **LLM-extracted** |
| `invalid_at` | world (`T`) | the time the fact stopped being true — nullable, **LLM-extracted** |

In code: `EntityEdge.expired_at` / `valid_at` / `invalid_at` / `reference_time`
(`graphiti_core/edges.py:271-287`), persisted verbatim
(`graphiti_core/models/edges/edge_db_queries.py:96-98, 161-163`) and indexed
(`graphiti_core/graph_queries.py:40`: index on `uuid, group_id, name, created_at, expired_at,
valid_at, invalid_at`). The paper states the same design as novel: *"this bi-temporal approach
represents a novel advancement in LLM-based knowledge graph construction"* (§2.1).

Strata's `invalid_at` / `superseded_by` covers the world axis and one transaction marker; Zep keeps
**both axes fully separated with four columns**. That is a genuine modelling improvement over ours —
but see the punchline below: on the read path Zep mostly doesn't use it.

### How a new fact retires an old one — exactly, in code

The mechanism is **LLM proposes, dates decide.** This is the most important finding in this spike.

1. **Two scoped hybrid searches fetch candidates.** For each extracted edge, Graphiti runs
   `EDGE_HYBRID_SEARCH_RRF` (BM25 + cosine) twice
   (`graphiti_core/utils/maintenance/edge_operations.py:526-571`):
   * *duplicate* candidates, hard-filtered to the edge's own endpoints either direction:
     `_pair_search_filter` builds `src ∈ {a,b} AND tgt ∈ {a,b}` (`:399-411`), then
     `_same_pair_edges` drops self-loops (`:414-421`);
   * *invalidation* candidates, searched **unscoped** (`SearchFilters()`, `:558-571`) minus the
     duplicate set.
   The paper justifies the scoping as complexity control: *"the hybrid search for relevant edges is
   constrained to edges existing between the same entity pairs … significantly reduces the
   computational complexity of the deduplication process"* (§2.2.2).
2. **One LLM call returns indices, not judgments-as-text.** `dedupe_edges.resolve_edge`
   (`edge_operations.py:982-990`) asks for `duplicate_facts: list[int]` and
   `contradicted_facts: list[int]` over a continuous index spanning both candidate lists
   (`:955-969`, `prompts/dedupe_edges.py:EdgeDuplicate`). The code then **validates the indices** and
   logs a warning on out-of-range values (`:994-1002`, `:1019-1026`).
3. **The decision is date arithmetic.** `_apply_temporal_invalidation` (`:715-745`) and
   `resolve_edge_contradictions` (`:748-783`) contain **no LLM call at all**:

```python
# edge_operations.py:727-743
if resolved_edge.invalid_at and not resolved_edge.expired_at:
    resolved_edge.expired_at = now                       # arrived already historical
if resolved_edge.expired_at is None:
    invalidation_candidates.sort(key=lambda c: (c.valid_at is None, ensure_utc(c.valid_at)))
    for candidate in invalidation_candidates:
        if candidate_valid_at_utc > resolved_edge_valid_at_utc:
            resolved_edge.invalid_at = candidate.valid_at  # NEW edge gets retired instead
            resolved_edge.expired_at = now
            break
return resolve_edge_contradictions(resolved_edge, invalidation_candidates)

# edge_operations.py:763-781  (paraphrased condition)
if (edge.invalid_at <= new.valid_at) or (new.invalid_at <= edge.valid_at):
    continue                                             # intervals already disjoint
elif edge.valid_at < new.valid_at:
    edge.invalid_at = new.valid_at                       # paper §2.2.3: t_invalid := t_valid of the invalidator
    edge.expired_at = utc_now()
    invalidated_edges.append(edge)
```

   So an edge is retired **only if the LLM flagged it *and* the world-time intervals actually
   overlap and order correctly.** If the new fact is *earlier* in the world than the old, the *new*
   fact gets retired instead. Zep's blog walks the out-of-order case: ingesting *"I divorced Jane
   last month"* before *"I married Jane in August 2005"* → *"we sort them by their `valid_at` dates …
   then invalidate the edge that occurred earlier in the real world"*
   (https://www.getzep.com/blog/beyond-static-knowledge-graphs/).

4. **Dates come from a small LLM pass, guarded by regex.** `_extract_edge_timestamps`
   (`:786-831`) is skipped if dates already exist, and its output goes through
   `normalize_future_temporal_bounds` (`utils/maintenance/temporal_edge_utils.py:44-77`) — a
   **deterministic** correction layer with three regexes (`_FUTURE_ASSERTION_RE`, `_DEADLINE_RE`,
   `_ONGOING_STATE_END_RE`, `:17-35`): future-tense plans get `valid_at = reference_time`, and a
   soft deadline ("will finish **by** Friday") is **stripped from `invalid_at`** unless the fact also
   says the state *ends*. Prompt-side rules match: *"Do NOT use a soft deadline … as `invalid_at`.
   Use a future date as `invalid_at` only when the fact describes an ongoing state that ends then"*
   (`prompts/extract_edges.py:183-193`).
5. **The contradiction prompt is a fossil record of over-invalidation.** `prompts/dedupe_edges.py`
   system line: *"NEVER mark facts as contradicted unless the new fact explicitly makes the old fact
   false"*, and the user prompt spends ~200 words on carve-outs: *"Do NOT treat additional activity,
   progress, status, or project-update facts as contradictions of role, ownership, responsibility,
   membership, or assignment facts. A person doing work on a project is compatible with that person
   remaining responsible for … the DRI for the project"*, plus *"Do NOT infer contradiction from
   recency alone, episode dates, meeting dates, weekly snapshots, section headings such as 'updates'
   or 'status changes', or a new event involving the same entities."* Worked examples include the
   negative case *"Bob ran 5 miles on Tuesday" / "Bob ran 3 miles on Wednesday" → neither duplicate
   nor contradiction*.
6. **History is preserved and re-narrated.** Nothing is deleted; `expired_at` marks retirement
   ("invalidated — not deleted", `graphiti/README.md`). The blog additionally documents fact
   rewriting: *"`Maria works as a junior manager` becomes `Maria used to work as a junior manager,
   until her promotion to a senior manager`"* so retrieval returns a coherent narrative — though the
   current code path I read only sets dates (`edge_operations.py:779-780`); no fact-rewriting prompt
   survives in `graphiti_core/prompts/` (v0.31.0 has no `invalidate_edges.py`; the blog links
   `prompts/extract_edge_dates.py` at commit `69964d3`, a file that no longer exists). Treat the
   re-narration as **documented-then-removed**, not current behaviour.

### What validity does *not* do on the read path

This is where Zep is weaker than it looks, and where Strata is stronger than the framing suggests.

* **No default suppression of invalidated facts.** `expired_at`/`invalid_at` appear only in the
  `RETURN` clauses of search queries (`search_utils.py:1469-1471, 1523-1525, 1656-1658`), never in a
  `WHERE`. Temporal predicates exist only as **opt-in** filters: `SearchFilters.valid_at` /
  `.invalid_at` / `.expired_at` / `.created_at` (`search_filters.py:62-65, 166-288`). "Currently
  valid" must be requested explicitly (`invalid_at IS NULL`).
* Zep then **prints the range into the block** and hands the reasoning to the reader model:
  ```
  # These are the most relevant facts and their valid date ranges
  # format: FACT (Date range: from - to)
  <FACTS>
    - Emily is experiencing issues with logging in. (2024-11-14 02:13:19+00:00 - present)
    - Account Emily0e62 made a failed transaction of 99.99. (2024-07-30 00:00:00+00:00 - 2024-08-30 00:00:00+00:00)
  </FACTS>
  ```
  (https://help.getzep.com/v4/retrieving-context.md). Note the **closed-range fact in the block**:
  superseded facts are *injected*, not filtered. The paper's constructor `χ` does the same:
  *"for each `e_i ∈ E_s`, `χ` returns the fact and `t_valid`, `t_invalid` fields"* (§3.2).
* The Zep repo's own harness prompt has to *teach* the model to use them: *"If the memories contain
  contradictory information, prioritize the most recent memory"* and *"Timestamps in memories
  represent the actual time the event occurred, not the time the event was mentioned in a message"*
  (`zep/benchmarks/longmemeval/zep_longmem_eval.py:29-44`, and quoted in the Mem0 dispute at
  https://github.com/getzep/zep-papers/issues/5).
* Zep's docs concede the point: *"The `valid_at` and `invalid_at` fields define the temporal bounds
  that Zep has derived for a fact. Applications can use these fields for history and point-in-time
  retrieval. **The fields do not establish that the source content or the derived fact is true.**"*
  (https://help.getzep.com/v4/facts.md).

### Does it scale? Zep's write-time LLM vs Strata's timer — the judgment

The premise in the brief — "Zep invalidates edges with LLM judgment at write time; Strata retires
rows with a deterministic merge law" — is **half wrong, and the half that is wrong is the
interesting part.**

* Zep's invalidation **decision** is deterministic date arithmetic (`resolve_edge_contradictions`,
  `edge_operations.py:748-783`). The LLM only (a) chooses which existing facts are *candidates for
  contradiction*, and (b) supplies `valid_at`/`invalid_at` strings that a regex layer then
  sanitises. Strata's law is the same shape: *cosine proposes, a law decides*. **We are not more
  deterministic than Zep; we are cheaper per write, and our proposal signal (cosine) is cruder than
  theirs (pair-scoped hybrid search + typed subject).**
* Where Zep's design scales is **candidate scoping**, not the LLM. Because the duplicate/contradiction
  comparison set is bounded by "same entity pair" plus ANN/BM25, the per-write LLM call compares
  ~5-20 facts regardless of store size. Write cost is **O(facts per episode)**, independent of
  database size. That is the property Strata currently lacks: our `_semantic_dupe` guard rebuilds an
  O(all-rows) matrix on every dirty write, i.e. write cost grows with the store — quadratic ingest.
* Where Zep's design does **not** scale is **ingest volume per compute budget, and offline/edge
  deployment.** ~28 LLM calls / 30k chars at ~8.7k input tokens each
  (https://github.com/GetKlai/klai/pull/1745); ~2 min per episode serial
  (https://renezander.com/guides/graphiti-add-episode/); minutes of staleness admitted by Zep's own
  docs; a hard dependency on a structured-output-capable LLM (*"particularly problematic when using
  smaller models"* — `graphiti/README.md`) and on a separate graph database (Neo4j 5.26 / FalkorDB
  1.1.2 / Neptune; Kuzu deprecated). None of that fits inside one 24 GB GPU shared with a quantized
  MoE, CPU-only embeddings, no cloud.
* Where Strata's design scales is **everything we care about**: zero hot-path LLM, timer batching,
  no extra process, deterministic and replayable retirement. Where it fails is exactly one thing:
  **the proposal step is unindexed**, so the retirement machinery degrades as the store grows —
  the same axis on which we already measured right-fact-in-block decaying ~3 points per doubling.

**Middle ground (this is the recommendation, elaborated in Portable ideas #4/#5/#6):**
keep Zep's *scoping* and Strata's *deciding*. Concretely — index-mediated candidate retrieval
(same-subject + same-scope + FTS5/ANN) to bound the comparison set; deterministic merge law as the
sole authority; and, optionally, a **deferred, budget-capped** LLM adjudicator that runs on the
existing timer for the residual gray band only, recording its verdict as provenance. Zep's own
trajectory supports this: the OSS engine added deterministic gates *in front of* the LLM
(`node_operations.py:840-891`: identity properties → exact name → MinHash/LSH Jaccard ≥ 0.9 with an
entropy floor → LLM only for the leftovers, `dedup_helpers.py:31-36, 53-86`). They moved toward
determinism; we should move toward indexing.

---

## Evaluation & published numbers (claimed vs reproduced)

### A. The paper (arXiv 2501.13956, Jan 2025) — as claimed

Models: **BGE-m3** for embedding *and* reranking; `gpt-4o-mini-2024-07-18` for graph construction;
`gpt-4o-mini` and `gpt-4o` as readers; judge = GPT-4o with the official LongMemEval question-specific
prompts (§4.1, §4.3). Retrieval: **top 20 edges + 20 nodes**, formatted into a context string
(§4). Experiments run Dec 2024 – Jan 2025.

**DMR (Table 1):** Recursive Summarization 35.3% · Conversation Summaries 78.6% · MemGPT 93.4% ·
**Full-conversation 94.4%** · **Zep 94.8%** (gpt-4-turbo); gpt-4o-mini: summaries 88.0% ·
full-conversation 98.0% · **Zep 98.2%**.

**LongMemEval_S (Table 2):**

| Memory | Model | Score | Latency | Latency IQR | Avg context tokens |
|---|---|---|---|---|---|
| Full-context | gpt-4o-mini | 55.4% | 31.3 s | 8.76 s | 115k |
| Zep | gpt-4o-mini | 63.8% | 3.20 s | 1.31 s | 1.6k |
| Full-context | gpt-4o | 60.2% | 28.9 s | 6.01 s | 115k |
| Zep | gpt-4o | **71.2%** | 2.58 s | 0.684 s | 1.6k |

**Table 3 (per question type, gpt-4o):** single-session-preference 20.0→56.7 (+184%) ·
single-session-assistant 94.6→80.4 (**−17.7%**) · temporal-reasoning 45.1→62.4 (+38.4%) ·
multi-session 44.3→57.9 (+30.7%) · knowledge-update 78.2→83.3 (+6.52%) · single-session-user
81.4→92.9 (+14.1%).

### B. What the paper itself concedes (read this before believing the headline)

* **DMR is dismissed by the authors themselves**: *"each conversation contains only 60 messages,
  easily fitting within current LLM context windows"*; *"significant weaknesses in the benchmark's
  design"*; *"Many questions contain ambiguous phrasing"*; and full-context (94.4%) essentially ties
  Zep (94.8%). So the "beats MemGPT" claim is a win over a baseline the paper's own full-context
  baseline also beats.
* They **could not reproduce MemGPT** on gpt-4o-mini, and on LongMemEval MemGPT produced no usable
  answers: *"we were unable to achieve successful question responses using this approach"* (§4.3.1).
* **The latency comparison is asymmetric against Zep**, not for it: network round-trips from a
  Boston laptop to `us-west-2` are inside Zep's latency and absent from the baselines (§4.3).
* The 18.5% is **relative** (60.2→71.2 = +11.0 points = +18.3% relative), which the abstract's
  "accuracy improvements of up to 18.5%" invites readers to misread as points.
* **Single-run, no variance** on LongMemEval; no repeats, no std, no contamination control described.
* The **regression is real and unexplained**: *"The decrease in performance for single-session-assistant
  questions … represents a notable exception"* (§4.3.2).

### C. The LoCoMo war — claimed vs reproduced (fully documented in public)

1. Mem0's paper (arXiv 2504.19413) reported Zep at 65.99% on LoCoMo.
2. Zep rebutted: *"Is Mem0 Really SOTA in Agent Memory?"* claimed **84%**, blamed Mem0's
   implementation (wrong user role model, timestamps appended to text instead of `created_at`,
   sequential instead of parallel searches), and attacked LoCoMo itself (16-26k tokens is not long;
   no knowledge-update questions; category 5 unusable; BLIP image descriptions; wrong speaker
   attribution; underspecified questions) — https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/
3. Mem0 counter-filed: **58.44% ± 0.20**, because Zep's calculation *"erroneously used a denominator
   that excluded Category 5 questions while including Category 5 correct answers in the numerator"*
   (a ~25.6-point inflation), plus prompt/template drift and single-run reporting —
   https://github.com/getzep/zep-papers/issues/5
4. **Zep conceded the arithmetic** (Chalef, 2025-05-12): *"The corrected score is **75.14% ± 0.17**
   (over 10 runs), with Zep outperforming Mem0 by ~10%"*, and the blog was amended with a correction
   banner. Issue closed *"due to inactivity"* 2025-05-19.
   Note the blog still carries an uncorrected headline ("outperforms Mem0 by 24%" in the meta
   description vs "by 10%" in the body) and a typo'd banner date ("December 9, **2015**").

**Lesson for Strata:** the vendor with the better benchmark *narrative* won an argument it had
numerically lost by 25 points. Our own harness must publish denominator definitions and repeat
counts, or we will do the same thing to ourselves.

### D. Zep's own in-repo sweep — the most useful number set for us

`zep/benchmarks/locomo/experiments/*/experiment_summary.json` (Dec 2025) is a **5-config budget
sweep**, 10 runs each, `gpt-4o-mini` reader *and* grader at **temperature 0**, cross-encoder rerank
on both scopes, LoCoMo 10 users × 35 sessions. Matches the blog
https://www.getzep.com/blog/the-retrieval-tradeoff-what-50-experiments-taught-us-about-context-engineering/:

| config (edges/nodes) | median context tokens | retrieval p50 / p95 (s) | accuracy mean ± std | complete / partial / insufficient |
|---|---|---|---|---|
| 5 / 2 | 347 | 0.149 / 0.332 | 69.62 ± 0.47 | 57.4 / 18.9 / **23.8** |
| 10 / 2 | 504 | 0.161 / 0.344 | 73.72 ± 0.41 | 62.8 / 16.6 / 20.6 |
| 15 / 5 | 756 | 0.199 / 0.503 | 77.06 ± 0.41 | 70.0 / 13.0 / 16.9 |
| 20 / 20 | 1,378 | 0.241 / 0.576 | 80.06 ± 0.33 | 75.8 / 11.0 / 13.2 |
| 30 / 30 | 1,997 | 0.189 / 0.437 | **80.32 ± 0.43** | **76.5** / 10.0 / 13.5 |

Readings: **+10.4 points for 4× the tokens, then +0.26 points for 1.5× more** — *"The curve
flattens, but it doesn't invert. More context doesn't hurt accuracy on this benchmark."*
Their sharper metric is **completeness**: *"At 5/2, we see 70% accuracy but only 57% completeness.
The agent is succeeding on some questions despite inadequate context: lucky guesses."*
And: *"latency isn't the binding constraint. Context cost and signal clarity are."*
Their conclusion: *"LoCoMo plateaus near 80% accuracy, but that's the benchmark's ceiling, not
Zep's."*

Two methodology notes vs our protocol: they run at **temperature 0** (both reader and judge,
`zep/benchmarks/locomo/benchmark_config.yaml`, `zep/benchmarks/longmemeval/zep_longmem_eval.py:280,306`)
where our protocol forbids 0; but they **do** repeat ≥3 (10 runs) and publish std, which matches our
repeat discipline. Their LongMemEval harness is 2 searches × limit 20 (`zep_longmem_eval.py:343-348`)
and their current eval harness uses `graph.get_context` with `MAX_CHARACTERS = 10_000` plus a
separately-prepended user summary (`zep/zep-eval-harness/config/evaluation_config/retrieval_strategy.py`).

### E. Zep's 2026 numbers, and why they are not comparable to their own 2025 numbers

https://www.getzep.com/research/ (page modified 2026-09-29):

* **LoCoMo 94.7%** (1,459/1,540), retrieval **87/155 ms** p50/p95, context **5,760 tokens**.
  Categories: single-hop 96.4, temporal 95.6, multi-hop 94.0, open-domain 79.2.
* **LongMemEval 90.2%** (451/500), retrieval **104/162 ms**, context **4,408 tokens**.
  Categories: assistant 96.4, user 94.3, knowledge-update 93.6, temporal-reasoning 90.2,
  preference 90.0, multi-session 83.5.
* **Auto search**: 86.5%, 115/173 ms, 2,680 tokens (−53%).
* Methodology: *"Reader: `gpt-5.4` (reasoning = medium). Judge: `gpt-5.4` with chain-of-thought
  grading. Multi-scope retrieval depth: 20 edges, 10 nodes, 10 episodes, 5 thread summaries,
  5 observations, cross-encoder reranking. … 0 failed tests."*

**Critical observation:** the paper's 71.2% became 90.2% by (i) swapping the reader from `gpt-4o` to
a 2026 reasoning model, (ii) swapping the judge to a reasoning model with CoT, (iii) **increasing
context 1.6k → 4.4k tokens**, and (iv) adding three more context shapes and a cross-encoder. Their
own repo sweep says +0.26 points for that token increase on LoCoMo, while a cross-encoder is a real
addition. **Nothing on that page isolates architecture**, and there are no repeats or error bars.
Same for LoCoMo 80.3% (repo, gpt-4o-mini) → 94.7% (research page, gpt-5.4 reader). This is exactly
the confound Strata's protocol kills (fixed reader, muted control arm, ≥3 repeats, budget swept
separately) — and it is worth saying out loud in our README: *Zep's headline moved 19 points without
a single word about the memory layer.*

### F. Third-party reproductions and audits

* **Emergence AI beat the number with plain RAG**: 86.0% (internal, 5.65 s) and, public,
  **EmergenceMem Simple 82.4% @ 7.12 s** and **Simple Faster 76.8% @ 2.97 s** — beating Zep on both
  accuracy *and* latency, with **100% on single-session-assistant vs Zep's 80.4%**. Their conclusion:
  *"advanced memory architecture appears to be overkill for LongMemEval"* and *"the best benchmark
  currently out there still isn't capturing important aspects of memory"* —
  https://www.emergence.ai/blog/sota-on-longmemeval-with-rag (their table quotes Zep's Table 3
  values exactly, so the two are directly comparable). Code: https://github.com/EmergenceAI/emergence_simple_fast
  Two tricks worth stealing: **retrieve whole sessions scored by NDCG of reranked turns**, and
  **two-pass answering** (extract events → answer).
* **Independent reproduction of the *harness***: Calvin Ku found GPT-4o-mini alone reaching 71.2% —
  *"That's a stunning outcome, as it's the exact same score Zep achieved using the much more powerful
  and expensive"* model — https://medium.com/asymptotic-spaghetti-integration/emergence-ai-broke-the-agent-memory-benchmark-i-tried-to-break-their-code-23b9751ded97
* **Benchmark-quality audits**: LoCoMo has *"a 6.4% wrong answer key and a judge that accepts 63% of
  wrong answers"* (https://agentos.sh/blog/memory-benchmark-transparency-audit/, echoed at
  https://cognitivx.io/blog/longmemeval-benchmark). Mastra flags that *"EmergenceMem's 86.00% is
  reported for an 'Internal' configuration and is not publicly reproducible"*
  (https://mastra.ai/research/observational-memory).
* **Every vendor scores itself**: *"every row here was produced by the vendor scoring its own system,
  not by one neutral harness running all four"* and *"the latency columns do not measure the same
  span: Zep reports retrieval, Mem0 reports a figure that holds at or under 1.1s"* —
  https://particula.tech/blog/agent-memory-frameworks-tested-mem0-zep-letta-cognee-2026.
  Zep's own docs make the same admission about LoCoMo: *"we're not huge fans of this benchmark."*

**Net assessment for Strata.** Zep's **71.2% is real but was never the differentiator** — a plain
RAG funnel beats it. Zep's **90.2% is not comparable to anything**. What is genuinely credible and
reproducible is the **in-repo budget sweep** (10 runs, mean ± std, tokens and latency reported
together) and the **completeness metric**. Our 0.761 oracle LongMemEval number sits *above* the
published Zep OSS-era number and *below* the 2026 vendor numbers, on a local 125B model — which is
the comparison we should be making publicly, with their table format.

---

## What Zep does better than Strata tier-3

Numbered: mechanism → code/doc path → why it matters for us.

1. **Validity ranges are rendered into the block, not just stored.** Mechanism: constructor `χ`
   emits `fact (t_valid – t_invalid)`; product renders `FACT (Date range: from - to)`.
   Path: paper §3.2; https://help.getzep.com/v4/retrieving-context.md;
   `zep/benchmarks/longmemeval/zep_longmem_eval.py:29-44`.
   *Why it matters:* it costs **zero retrieval compute** and targets the two categories where we are
   weakest and where Zep's biggest published gains were (temporal-reasoning 45.1→62.4,
   knowledge-update 78.2→83.3). We already store `invalid_at`; we mostly don't show it.

2. **A reader instruction that disambiguates event-time from mention-time.** Mechanism: the response
   prompt states *"Timestamps in memories represent the actual time the event occurred, not the time
   the event was mentioned in a message"* and *"if contradictory information, prioritize the most
   recent memory"*. Path: quoted in https://github.com/getzep/zep-papers/issues/5 and in
   `zep/benchmarks/longmemeval/zep_longmem_eval.py:261-270`.
   *Why it matters:* it is a one-line, testable change to our injected block header, and it is
   plausibly a chunk of Zep's temporal-reasoning delta — i.e. prompt engineering, not graph magic.

3. **Relative-date resolution at write time against a reference timestamp.** Mechanism: every episode
   carries `reference_time`; a small LLM pass converts "two weeks ago" / "last summer" into absolute
   ISO bounds, with deterministic regex guards for future tense and soft deadlines.
   Path: paper §2.1, §6.1.5; `graphiti_core/utils/maintenance/temporal_edge_utils.py:17-77`;
   `edge_operations.py:786-831`; https://www.getzep.com/blog/beyond-static-knowledge-graphs/.
   *Why it matters:* Strata stores ~160-char facts with a turn timestamp and recency decay; we do not
   resolve in-text relative dates. LongMemEval's temporal category is full of them.

4. **Candidate scoping makes near-dupe/contradiction detection store-size-independent.** Mechanism:
   duplicate search is hard-filtered to the edge's own endpoints; invalidation search is a separate
   ANN+BM25 pass; both are `EDGE_HYBRID_SEARCH_RRF`.
   Path: `edge_operations.py:399-421, 526-586`; paper §2.2.2 (explicit complexity argument).
   *Why it matters:* this is precisely the fix for our quadratic `_semantic_dupe` matrix rebuild.
   Their *scoping* is portable; their *LLM* is not.

5. **Deterministic gates in front of every expensive judgment.** Mechanism: node resolution runs
   identity-property match → exact normalized-name match → MinHash/LSH Jaccard ≥ 0.9 gated by a
   Shannon-entropy specificity floor (`_NAME_ENTROPY_THRESHOLD = 1.5`, `_MIN_NAME_LENGTH = 6`) →
   LLM **only for the residual**. Edge dedupe has an exact-text fast path.
   Path: `node_operations.py:816-905`, `dedup_helpers.py:31-36, 53-135`,
   `edge_operations.py:940-951`.
   *Why it matters:* the entropy floor is a cheap, principled trick we lack — refusing to merge
   short/low-specificity strings is exactly the failure mode our subject veto hand-codes.

6. **Multiple granularity tiers to rank across, with a character budget.** Mechanism: six context
   types (facts, entity summaries, episodes, observations, thread summaries, user summary) ranked
   *globally* and packed to `max_characters`; block shape varies per query instead of fixed
   per-type counts. Path: https://help.getzep.com/v4/searching-the-graph.md#auto-search,
   https://help.getzep.com/v4/context-types.md,
   https://www.getzep.com/blog/smart-context-assembly-fewer-tokens-higher-quality/.
   *Why it matters:* our block is 96 notes / 24k chars, pinned per conversation, uniform in shape.
   Their published 54%-token-cut-for-8-points is a design point we can test on our own harness.

7. **Provenance as traversable structure, not a column.** Mechanism: episodes are non-lossy raw
   nodes; `EntityEdge.episodes` lists the episodes supporting a fact and `EpisodicNode.entity_edges`
   the reverse; bidirectional indices enable *"both forward and backward traversal"*; product nodes
   return `episode_uuids` (newest first, truncated at 100) for citation.
   Path: paper §2.1; `graphiti_core/edges.py:267-270`; `graphiti_core/nodes.py:318-332`;
   `edge_operations.py:69-113`; https://help.getzep.com/v4/searching-the-graph.md (Nodes);
   https://help.getzep.com/v4/source-traceability.md.
   *Why it matters:* our provenance is a per-row pointer. Theirs supports "show me the source of this
   fact" and "show me everything this turn produced" as *queries*, which is what a memory UI and a
   poisoning-recovery story both need.

8. **A typed ontology with mechanical enforcement.** Mechanism: Pydantic entity/edge types per graph;
   `edge_type_map` signatures; `enforce_edge_type_signatures` renames or drops non-conforming edges;
   `identity_properties` as hard dedup keys. Path: `edge_operations.py:649-712`,
   `node_operations.py:823` + `dedup_helpers.py:207-259`;
   https://help.getzep.com/v4/customizing-graph-structure.md.
   *Why it matters:* our `class` shelf is a keyword map with a 2-token margin (18% wild). A typed
   schema with a declared domain/range would let us *validate* curation output instead of merely
   bucketing it.

9. **Retrieval-signal diversity beyond bm25+cosine.** Mechanism: BFS from up to 5 seed
   nodes/episodes (recent-episode seeding is the documented pattern), episode-mention frequency
   reranking, node-distance reranking from a centroid, cross-encoder with knee-cut filtering.
   Path: paper §3.1-3.2; `search_config.py:29-77`; `search_utils.py:1793-1932`;
   `zep/legacy/src/lib/search/mmr.go` (LangChain-derived MMR);
   https://help.getzep.com/v4/searching-the-graph.md.
   *Why it matters:* we have exactly two signals and one fusion. Their "recent episodes as BFS seeds"
   is a *recency* mechanism structurally different from our recency weight, and it is what lets a
   block be locally coherent around the current conversation.

10. **Operational maturity: async ingestion you can observe.** Mechanism: per-episode debug logs and
    ingestion traces, `graph.episode.get_debug_logs`, webhooks, status endpoints, batch API,
    `zep-ingest` that encodes the sharp edges (episode limits, chunking, timestamps silently
    defaulting to ingest time, aliases that never merge).
    Path: https://help.getzep.com/v4/check-data-ingestion-status.md,
    https://help.getzep.com/v4/debug-mode.md, https://help.getzep.com/v4/webhooks.md,
    `zep/ingestion/README.md`.
    *Why it matters:* our curation loop runs on a timer; when it stalls or mis-merges we have no
    per-turn trace equivalent. Every one of our debugging sessions would be shorter with an
    "episode → what it produced → what it invalidated" trace.

11. **Explicit memory-poisoning / untrusted-context posture.** Mechanism: docs mandate the block go
    through the provider's *untrusted-data* channel and never a system/developer message; content
    policies classify episodes; write controls, scoped retrieval, action authorization, recovery.
    Path: https://help.getzep.com/v4/memory-security.md,
    https://help.getzep.com/v4/retrieving-context.md ("Using context"),
    https://help.getzep.com/v4/content-policies.md.
    *Why it matters:* we inject a 96-note block; we should be able to state this policy in our README
    and in the harness ("muted control" is only half of it).

---

## What Strata does that Zep does not

1. **No LLM in the request hot path, and none per write in the default deployment.** Zep runs ~6 LLM
   passes per episode (https://help.getzep.com/v4/how-graph-creation-works.md) and ingests in
   minutes (*"Zep ingestion can take a few minutes"* —
   https://help.getzep.com/v4/retrieving-context.md). Strata: zero hot-path LLM, curation on a
   30-min timer. Neither Zep Cloud (managed, credit-billed) nor Graphiti OSS (LLM required,
   *"works best with LLM services that support Structured Output … particularly problematic when
   using smaller models"* — `graphiti/README.md`) can run extraction on one 24 GB GPU that is already
   holding a quantized 125B MoE.
2. **Local-first, single-file store, encrypted at rest.** Zep OSS requires an external graph database
   (Neo4j 5.26 / FalkorDB 1.1.2 / Neptune; Kuzu deprecated — `graphiti/README.md`); CE required four
   services (`zep/legacy/docker-compose.ce.yaml`). Strata is SQLite+FTS5+ vectors in one encrypted
   file. Also: Zep's headline numbers are from a **hosted** service, so latency and quality are not
   reproducible locally at all.
3. **A deterministic, replayable retirement law** (subject veto → payload containment → Jaccard drift
   → supersede-if-strictly-richer) that never depends on a model's mood. Zep's retirement depends on
   an LLM returning an index list, and its prompt is a visible scar tissue of over-invalidation
   failures (`graphiti_core/prompts/dedupe_edges.py`: DRI/status-update/weekly-snapshot carve-outs).
   A law can be unit-tested and re-run after a bug fix; a prompt cannot.
4. **Per-row confidence, recency and provenance as *read-path blend inputs*.** Graphiti's
   `EntityEdge` has `name, fact, fact_embedding, episodes, expired_at, valid_at, invalid_at,
   reference_time, attributes` (`graphiti_core/edges.py:263-290`) — **no confidence field**; Zep
   Cloud's edge `score` is a *reranker output*, not a stored belief
   (https://help.getzep.com/v4/searching-the-graph.md#reranker-score). Our blend uses belief +
   recency + provenance, which is a richer ranking prior than rank-only.
5. **Read-path near-dupe suppression.** Zep deliberately over-includes — *"Zep includes more facts,
   and the model ignores the facts that are not relevant"*
   (https://help.getzep.com/v4/retrieving-context.md) — and suppresses duplicates only at write time.
   We suppress at read time, which is the only mechanism that protects a fixed-size block as the
   store grows (our measured ~3-point/doubling decay).
6. **A pinned per-conversation block (cacheable prefix).** Zep had to publish a whole post on this
   problem — *"Agent memory placement can cut your token bill up to 2x … putting it in the system
   prompt breaks prompt caching and re-bills the whole conversation each turn"*
   (https://www.getzep.com/blog/where-you-put-memory-in-the-prompt-can-cut-your-token-bill-up-to-2x/)
   — and their docs instruct the app to place the block after history and *"Replace the previous
   turn's block instead of appending a second one"*
   (https://help.getzep.com/v4/retrieving-context.md). We get this by construction.
7. **Store-size scaling evidence.** Zep publish accuracy vs *budget* and vs *latency*, never accuracy
   vs **store size** — every published run is a fixed 10-user LoCoMo set or a per-user LongMemEval
   haystack. Strata's right-fact-in-block decay curve (0.977@600 → 0.919@2655) is a measurement Zep
   has not made and, given their per-user graph model, may not need. It is also the measurement that
   tells us *which* of their ideas are safe.
8. **Byte-identical default path + opt-in discipline and a muted control arm.** `AGENTS.md` requires
   *"A new feature is opt-in, and the default path stays byte-identical to the last release"*, and
   our protocol mutes memory (`X-Strata-Memory: off`) to get a control arm. Zep's eval pages report
   no control arm, no repeats, no contamination control; their repo harness does repeat (10 runs) but
   at temperature 0.
9. **Memory budget / pressure-driven curation.** Strata has an opt-in `memory.max_memories` budget
   with pressure reporting; Zep/Graphiti have **no forgetting**: invalidation retires claims but the
   graph only grows, and even communities need *"periodic community refreshes"* because dynamic label
   propagation drifts (paper §2.3). Nobody in Zep's stack bounds total memory.
10. **CPU-only, pinned, hashed embedder with a measured A/B arm.** `bge-small-en-v1.5` ONNX, 133 MB,
    SHA-256 pinned, byte-identical default when absent, and a unified A/B mode with a pinned head
    block to prevent cross-arm contamination. Graphiti's default embedder is a **cloud API**
    (`text-embedding-3-small`, 1024-dim — `graphiti_core/embedder/openai.py:24`,
    `graphiti_core/embedder/client.py:23`), and the paper used BGE-m3 via its own service (paper
    §4.1). Zep Cloud ships *"proprietary … embedding models"*
    (https://help.getzep.com/v4/zep-vs-graphiti.md) — unmeasurable by us.

---

## Portable ideas for Strata tier-3

Ranked by (expected gain × confidence) ÷ cost. "Our harness" = 500 LongMemEval questions × 3 repeats
at temp 0.8 with the official checker and the muted control arm, per our standing protocol.

**1. Render validity ranges into the injected block, and add the event-time/mention-time instruction.**
*Mechanism:* Zep's `χ` returns `fact + t_valid + t_invalid`; the block renders
`FACT (from – to)` and the prompt says timestamps are event time, not mention time, and to prefer the
most recent on contradiction.
*Mapping:* we already have `invalid_at`/`superseded_by`; append ` (valid <date>–<date|now>)` to rows
whose validity is bounded, and add two header lines to the block: one defining the range, one
instructing "prefer the fact whose range covers the asked-about time; a closed range means it was
superseded, not deleted".
*Cost:* zero retrieval compute; ~18-28 tokens per bounded row; ~40 tokens of header.
*Measure:* temporal-reasoning and knowledge-update slices of our harness, 3 repeats, plus
right-fact-in-block unchanged (proof we didn't buy it with tokens). Expect +1-3 on
temporal-reasoning. Also re-run the budget sweep to confirm the extra tokens cost nothing (our
measured budget saturation says they shouldn't).
*Risk:* token inflation in the 24k block; and a range on a *wrong* date (bad `valid_at` from the
curation loop) can actively mislead — so gate on rows where validity is confidently set. This is the
single highest value/cost item here.

**2. Add a completeness ("context sufficiency") judge to the harness — Zep's Complete / Partial /
Insufficient rubric.**
*Mechanism:* an LLM grades *the retrieved block alone* against the gold answer: did the block contain
everything needed? Independent of the model's answer.
*Mapping:* we already assert ~70% of residual errors are reasoning failures with full evidence
present; this turns that assertion into a per-question label and makes the
**accuracy − completeness gap our "guessing rate"**.
*Cost:* one extra judge call per question per arm (1,500 calls per run, offline, never in the loop).
*Measure:* it *is* the measurement. Report the Zep-shaped table (accuracy ± std, completeness
breakdown, tokens, latency) so our numbers are directly comparable to Zep's sweep, and re-derive the
retrieval-vs-reasoning split from data instead of from spot checks.
*Risk:* judge drift — pin judge model + prompt, run 3 repeats, publish the false-positive rate
(AgentOS measured ~1% on LongMemEval-S; treat >3% as uninformative).

**3. Re-present the budget result as Zep's curve, and publish tokens + latency next to every accuracy
number.**
*Mechanism:* their 5-config table (347→1,997 tokens; 69.62±0.47 → 80.32±0.43; p50 149→241 ms).
*Mapping:* we already have "bigger budget = zero gain"; express it in the same coordinates
(x = median block tokens, y = accuracy mean ± std over ≥3 repeats, columns = completeness and p50/p95).
*Cost:* harness-only; no product change.
*Measure:* 5 block sizes × 3 repeats. Then state the disagreement precisely: Zep's curve
*"flattens but doesn't invert"* at the **budget** axis on fixed-size stores, while ours decays
~3 points per **store-size** doubling at fixed budget. Those two facts are compatible and both are
ours to publish.
*Risk:* none but time. Also adopt their honesty about the benchmark ("we're not huge fans of this
benchmark") in our README.

**4. Kill the quadratic near-dupe guard by indexing the proposal step (adopt Zep's scoping, keep our
law).**
*Mechanism:* Zep never compares a new fact against "everything" — duplicate candidates come from a
pair-scoped hybrid search (`_pair_search_filter`, `_same_pair_edges`,
`edge_operations.py:399-421`), and the exact-text fast path short-circuits before any model call
(`:940-951`).
*Mapping:* three tiers before the merge law — (a) exact normalized text key; (b) **hard-scope** the
comparison set to `scope` + normalized subject (+ optionally `class`) using a SQLite index;
(c) FTS5/trigram or token-overlap prefilter inside that set; (d) cosine only on the surviving
candidates; (e) the existing merge law decides.
*Cost:* ~150-250 LOC, one index, CPU-only, **no LLM**. Removes the O(all-rows) matrix rebuild per
dirty write.
*Measure:* ingest wall-clock at 1k / 5k / 20k rows (must become linear); near-dupe recall/precision
held constant against the labelled family at rows 2550-2556 and a hand-built paraphrase set;
LongMemEval unchanged (it should be a pure performance win).
*Risk:* recall loss on paraphrases that share no subject string — mitigate by keeping the unscoped
cosine pass as a *fallback* arm and measuring both.

**5. Emit a normalized `subject` column from the curation loop and use it as the scoping key (and as
the veto's anchor).**
*Mechanism:* Zep's whole invalidation story rests on facts being attached to resolved entities with
optional `identity_properties` as hard keys
(`node_operations.py:823` + `dedup_helpers.py:207-259`;
https://help.getzep.com/v4/prepare-data-for-ingestion.md).
*Mapping:* one extra field in the proposal schema (same LLM call, one more key). Subject becomes:
the scoping key for idea 4, the anchor for the merge law's subject veto (currently inferred from
text), and a filter dimension for `memory_search` and the UI.
*Cost:* schema migration + prompt field; no extra inference.
*Measure:* near-dupe precision should *improve* (fewer cross-subject merges); knowledge-update slice;
and a hand-scored 50-row sample of subject extraction accuracy.
*Risk:* a bad subject silently hides the row from its own dupe set. Mitigate with Zep's documented
bias — *"prefers under-merge over over-merge"* — i.e. subject mismatch vetoes a **merge**, never a
**proposal**.

**6. A deferred, budget-capped LLM adjudicator for the gray band — the actual middle ground.**
*Mechanism:* Zep's `resolve_edge` prompt is the best available free specification of "what counts as
a contradiction", including the carve-outs that its own failures produced (progress/status/snapshot
facts do not contradict role/ownership/DRI facts; recency alone is not evidence;
`Bob ran 5 miles Tuesday` vs `3 miles Wednesday` = neither duplicate nor contradiction)
— `graphiti_core/prompts/dedupe_edges.py`.
*Mapping:* only rows where the merge law **abstains**, or where cosine lands in a declared gray band,
go to a nightly adjudicator with a hard cap on calls (cap = 0 keeps today's behaviour byte-identical).
Record the verdict in provenance (`retired_by = law | llm`) so the two are separable in analysis.
*Cost:* ≤ cap LLM calls/night on the timer, never in the hot path.
*Measure:* knowledge-update and temporal-reasoning slices; a hand-labelled 100-pair
contradiction/not-contradiction set scored precision/recall for law-only vs law+adjudicated;
**over-invalidation must be measured explicitly** (count superseded rows that a human says should
survive) — that is the failure Zep's prompt is fighting and it is invisible in an aggregate score.
*Risk:* nondeterminism in a system whose selling point is determinism. Mitigation: the law stays
authoritative on appeal, every adjudication is logged with its inputs, and the arm is opt-in.

**7. Deterministic date-normalization guards before trusting any extracted date.**
*Mechanism:* `normalize_future_temporal_bounds` + three regexes: future-tense plans get
`valid_at = reference_time`, and a soft deadline ("by Friday") is stripped from `invalid_at` unless
an end-of-state word appears (`temporal_edge_utils.py:17-77`; prompt rules
`prompts/extract_edges.py:183-193`).
*Mapping:* a ~40-line pure function applied to whatever the curation loop proposes for
`valid_at`/`invalid_at`, plus a fixed unit corpus ("will ship by Q3", "unavailable until Dec 25",
"moved to Berlin two weeks ago", "born June 23 1912").
*Cost:* trivial, CPU-only, deterministic, testable.
*Measure:* unit corpus (30-50 sentences) + temporal-reasoning slice. This is the hygiene fix that
makes idea 1 safe to ship.
*Risk:* near zero.

**8. Sweep the RRF constant — Zep uses `k=1`, we use `k=60`.**
*Mechanism:* `1/(i + rank_const)` with `rank_const=1` (`search_utils.py:1776-1781`) and the identical
shape in the retired CE (`legacy/src/lib/search/rrf.go:15-21`). With k=1 the fusion is dominated by
each arm's top hits; with k=60 it is a gentle consensus vote.
*Mapping:* one parameter in our fusion.
*Cost:* ~5 LOC; a 4-point sweep (k ∈ {1, 5, 20, 60}) on the harness.
*Measure:* turn-top1 and right-fact-in-block per k, 3 repeats; also per query class, because we
already know from the `lme_retrieval_ab.py` A/B that a single global rule moves
knowledge-update and multi-session in opposite directions.
*Risk:* none; it may just confirm k=60 — that is a publishable negative result and a nice contrast
to document against Zep.

**9. Character-budget packing across heterogeneous rows (their "auto search"), instead of fixed note
counts.**
*Mechanism:* rank everything globally, then greedily pack to `max_characters` (default 2,500; their
eval harness uses 10,000), returning `truncated`. Block shape varies per query
(https://help.getzep.com/v4/searching-the-graph.md#auto-search,
`zep/zep-eval-harness/config/evaluation_config/retrieval_strategy.py`).
*Mapping:* replace "96 notes / 24k chars" with a character budget plus per-`class` soft caps so no
shelf monopolises the block; keep the pinning.
*Cost:* moderate — a cross-class ranker plus a greedy packer.
*Measure:* the budget curve from idea 3, with block *shape* as the varied axis; report tokens
explicitly (this is where a 125B-local model diverges most from a cloud service — our tokens are the
expensive resource).
*Risk:* adaptivity can drop multi-session recall (Zep's own weakest category is multi-session, 83.5%
vs 96.4% for assistant). Measure multi-session separately.

**10. A timer-produced rollup tier (entity summary / observation analogue) on its own shelf.**
*Mechanism:* Zep regenerates node summaries *"from the new evidence"*
(https://help.getzep.com/v4/how-graph-creation-works.md step 6), and Observations are
*"durable, evidence-backed … deduplicated and merged: when new evidence fits an existing observation,
the existing observation is regenerated … when a newer observation supersedes an older one, the older
observation is retired"*, read-only, derived
(https://help.getzep.com/v4/observations.md).
*Mapping:* the curation loop already runs on a timer; let it also emit/refresh rollup rows keyed by
subject or shelf, marked `derived`, and rank them alongside atomic facts.
*Cost:* a handful of LLM calls per timer tick, batched — the pattern we already have.
*Measure:* single-session-preference and multi-session slices — exactly where the paper's biggest
gains were (20.0→56.7 and 44.3→57.9 with gpt-4o). Guard against crowding: right-fact-in-block must
not fall.
*Risk:* rollups are lossy and can *replace* the atomic fact that was actually needed; keep them
additive and capped in the block.

**11. BM25-only fallback when the embedder is unavailable (and say so in the block).**
*Mechanism:* *"If the graph embedder is unavailable, search continues with BM25 full-text search …
The vector leg is omitted. Recall can be lower"*
(https://help.getzep.com/v4/searching-the-graph.md).
*Mapping:* we already have the semantic-off arm header; turn it into a runtime degradation path with
a counter, so a cold/failed ONNX model degrades instead of erroring.
*Cost:* small.
*Measure:* our semantic-off A/B arm *is* the measured floor — quote it as the worst-case number.
*Risk:* silent quality loss without the counter.

### Explicitly not portable

* **The graph itself** (entity resolution, episodic edges, communities via label propagation,
  node-distance/BFS rerankers). At ~2.6k rows on one encrypted SQLite file, a graph DB plus
  per-episode entity resolution buys us the failure mode (mis-resolution) without the payoff, and our
  measured ceiling is *reasoning* (≈70% of errors have full evidence present), not linking.
  Communities are also admitted-stale: *"periodic community refreshes remain necessary"* (paper §2.3).
* **Per-write LLM extraction as a default.** ~28 calls / 30k chars at ~8.7k input tokens each
  (https://github.com/GetKlai/klai/pull/1745) does not fit our GPU budget.
* **Cross-encoder reranking as the default** — Zep's best numbers lean on it
  (`zep/benchmarks/locomo/benchmark_config.yaml`), and we measured +0.040 for a reranker; keep it as
  the opt-in arm, not the default, because the GPU is the shared scarce resource.
* **Cloud-only features**: Observations, thread summaries, Konig, ABAC policy sets, dashboard —
  product surface, not mechanisms we can measure locally.

### The central judgment, restated

Zep's write-time LLM and Strata's timer-law are **not opposites**. Both are *propose-then-decide*,
and in both the deciding step is deterministic arithmetic over dates. The real difference is that
Zep's **proposal** is indexed, scoped, typed and (mostly) cheap-to-correct, while ours is a global
unindexed similarity; and Zep's **decide** step inherits the LLM's contradiction judgement as a
side-channel (the index list), which ours keeps out of the law entirely. So: **steal the scoping and
the rendering; keep the law; and if you want Zep's last few points on knowledge-update, buy them
with a capped, logged, off-hot-path adjudicator rather than with per-write inference.** Their own
engine is converging on that shape — deterministic gates before every LLM call
(`node_operations.py:840-891`, `dedup_helpers.py:31-36`).

---

## Search log

Web searches (Brave Search API, 2026-10-14, `extra_snippets` on), in order:

1. `Zep: A Temporal Knowledge Graph Architecture for Agent Memory arXiv 2501.13956`
2. `Zep agent memory Deep Memory Retrieval 94.8% LongMemEval 18% latency`
3. `Zep memory benchmark results contested reproduction critique`
4. `Zep temporal knowledge graph architecture agent memory arXiv`
5. `Zep paper LongMemEval 71.2% accuracy 18% latency reduction`
6. `Zep Graphiti edge invalidation valid_at invalid_at expired_at bitemporal`
7. `Zep community edition deprecated Graphiti open source strategy`
8. `Emergence AI SOTA LongMemEval RAG 86% vs Zep 71.2%`
9. `Mem0 vs Zep LoCoMo benchmark comparison 2025 accuracy latency`
10. `Zep blog response Mem0 comparison memory benchmark criticism`
11. `help.getzep.com search rerankers rrf mmr cross_encoder node_distance episode_mentions`
12. `help.getzep.com context block construction graph search user graph thread`
13. `Zep vs Graphiti comparison page getzep docs`
14. `Graphiti number of LLM calls per episode ingestion cost latency knowledge graph`
15. `Graphiti self-hosted requirements Neo4j FalkorDB operational cost agent memory`
16. `Zep LongMemEval single-session-assistant regression temporal reasoning criticism`
17. `Graphiti add_episode synchronous write path latency minutes ingestion async`

Pages/documents fetched and read:

* https://arxiv.org/abs/2501.13956 and https://arxiv.org/html/2501.13956v1 (full paper text, all 3 tables, appendix prompts)
* https://help.getzep.com/llms.txt, https://help.getzep.com/v4/llms.txt (docs index)
* https://help.getzep.com/v4/searching-the-graph.md (search scopes, rerankers, filters, BFS, auto search, relative-time classifier, embedder fallback)
* https://help.getzep.com/v4/retrieving-context.md (Context Block format, recall-vs-precision, caching placement, ingestion-lag caveat)
* https://help.getzep.com/v4/how-graph-creation-works.md (6-step write pipeline, under-merge preference)
* https://help.getzep.com/v4/facts.md (four timestamps, edge names, "fields do not establish … true")
* https://help.getzep.com/v4/observations.md (derived tier, dedupe/merge/supersede, read-only)
* https://help.getzep.com/v4/zep-vs-graphiti.md (boundary table, Konig, proprietary models)
* https://www.getzep.com/research/ (2026 numbers + methodology, page modified 2026-09-29)
* https://www.getzep.com/blog/beyond-static-knowledge-graphs/ (bi-temporal design, out-of-order episodes, fact re-narration)
* https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/ (84%→75.14% correction, LoCoMo flaws, latency dispute)
* https://github.com/getzep/zep-papers/issues/5 (Mem0's 58.44% re-evaluation, full thread incl. Chalef's concession)
* https://www.getzep.com/blog/the-retrieval-tradeoff-what-50-experiments-taught-us-about-context-engineering/ (budget sweep, completeness metric, one-shot retrieval philosophy)
* https://www.getzep.com/blog/smart-context-assembly-fewer-tokens-higher-quality/ (auto search, 86.5% @ 2,680 tokens vs 94.7% @ 5,760)
* https://www.emergence.ai/blog/sota-on-longmemeval-with-rag (independent comparison table, "overkill" conclusion)

Code read locally (clones listed at top): `zep/README.md`, `zep/legacy/**`
(`lib/graphiti/service_ce.go`, `lib/search/rrf.go`, `lib/search/mmr.go`, `store/memory_ce.go`,
`store/migrations/000000000001_database_setup.up.sql`, `models/fact_common.go`, `zep.yaml`,
`docker-compose.ce.yaml`, `Dockerfile.ce`), `zep/benchmarks/longmemeval/**`,
`zep/benchmarks/locomo/**` (config + 5 experiment summaries), `zep/zep-eval-harness/**`,
`zep/ingestion/README.md`, `zep/ontology/default_ontology.py`, and `graphiti/graphiti_core/**`
(`graphiti.py`, `edges.py`, `nodes.py`, `utils/maintenance/{edge_operations,node_operations,
dedup_helpers,temporal_edge_utils,community_operations}.py`, `utils/bulk_utils.py`,
`search/{search_config,search_config_recipes,search_filters,search_utils,search}.py`,
`prompts/{dedupe_edges,extract_edges}.py`, `models/edges/edge_db_queries.py`, `graph_queries.py`,
`llm_client/config.py`, `embedder/{openai,client}.py`, `README.md`).

---

## Unverified claims

* **Zep Cloud's proprietary retrieval differs from OSS in ways we cannot see.** "Proprietary
  extraction LLMs, reranker, and embedding models" and Konig are asserted by Zep's own docs
  (https://help.getzep.com/v4/zep-vs-graphiti.md); no code, paper, or independent measurement exists
  for how much of 90.2% / 94.7% comes from them vs the reader-model swap. **Treat all 2026 vendor
  numbers as unaudited.**
* **The 2026 LongMemEval/LoCoMo numbers have no published repeats or variance** (single figure each;
  "0 failed tests"), unlike Zep's own repo experiments (10 runs ± std). Whether 90.2% survives 3
  repeats at temp 0.8/1.0 is untested by anyone outside Zep.
* **Fact re-narration on invalidation** (`"Maria used to work as a junior manager, until her
  promotion…"`) is documented in https://www.getzep.com/blog/beyond-static-knowledge-graphs/ (2024)
  but I found **no surviving prompt or code path** in `graphiti` v0.31.0 that rewrites the fact text;
  `edge_operations.py:779-780` only sets dates, and the `prompts/extract_edge_dates.py` file the blog
  links (at commit `69964d3`) no longer exists. Either it moved into Zep Cloud's proprietary layer or
  it was removed. Not verified either way.
* **Zep Cloud's Context Block does not filter invalidated facts by default** — inferred from the
  documented sample block containing a closed-range fact
  (https://help.getzep.com/v4/retrieving-context.md) and from date filters being opt-in
  (https://help.getzep.com/v4/searching-the-graph.md#datetime-filtering). I did not observe a live
  API response; there may be server-side default behaviour not in the docs.
* **`_apply_temporal_invalidation` coverage in the OSS bulk path** — `add_episode_bulk`'s docstring
  claims invalidation happens, while `bulk_utils.py:578` comments *"For now we won't track edge
  invalidation"*. I read the call graph but did not execute it, so I cannot say whether the bulk path
  persists `expired_at` on invalidated edges or only sets them in memory.
* **Emergence AI's 86.0%** is their closed "Internal" configuration; Mastra explicitly flag it as not
  publicly reproducible (https://mastra.ai/research/observational-memory). Only Simple (82.4%) and
  Simple Faster (76.8%) have released code (https://github.com/EmergenceAI/emergence_simple_fast),
  which I did not run.
* **LoCoMo quality figures** ("6.4% corrupted answer keys", "judge accepts ~63% of wrong answers")
  come from secondary write-ups (https://agentos.sh/blog/memory-benchmark-transparency-audit/,
  https://cognitivx.io/blog/longmemeval-benchmark) whose primary source I did not read.
* **Third-party Graphiti cost/latency numbers** (28 LLM calls / 30k chars, ~8.7k input tokens/call,
  ~2 min/episode, ~50 episodes/min at concurrency 10) are single-project self-reports
  (https://github.com/GetKlai/klai/pull/1745, https://renezander.com/guides/graphiti-add-episode/,
  https://pub.towardsai.net/i-needed-memory-for-my-ai-agent-and-found-this-knowledge-graph-framework-55c94880820e),
  not measured by us and not measured on graphiti-core 0.31.0.
* **Graphiti's default model names** (`DEFAULT_MODEL = 'gpt-6-luna'`,
  `llm_client/openai_base_client.py:35`) are what the *current* checkout defaults to; the paper's
  numbers used `gpt-4o-mini` for construction, and Zep Cloud's models are proprietary. Do not infer
  paper numbers from OSS defaults.
* **Whether Zep's temporal-reasoning gain is mostly the rendered date ranges + prompt** is my
  hypothesis, supported by the harness prompt text and by Zep's own category deltas, but not
  ablated by anyone I found. Idea 1 in "Portable ideas" is the experiment that would settle it for
  Strata.
* **Strata-side numbers** quoted for comparison (0.761 oracle LME, +0.040 reranker, ~3 points per
  store doubling, 18% wild, ~70% reasoning errors, quadratic `_semantic_dupe`) come from our own
  prior sessions and measurements, not re-verified in this spike.
