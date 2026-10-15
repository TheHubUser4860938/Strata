# Cognee (topoteretes/cognee) — deep read for Strata tier-3

**Clone:** `https://github.com/topoteretes/cognee` @ `f154b32a9e54` (tag **v1.6.4**, committed Tue 13 Oct 2026
17:20 +0200, `chore: Release v1.6.4 (#5759)`) — 170 MB, read locally at `/tmp/opencode/research/cognee`.
**License:** Apache-2.0 (`LICENSE`). **Stars:** 32.3k on the README badge at fetch time; an independent
comparison recorded 31,126 stars on 2026-09-28
(https://mnemoverse.com/docs/library/ai-memory-solutions-2026-q3). **Funding:** $7.5M seed, Feb 2026
(https://www.cognee.ai/blog/cognee-news/cognee-raises-seven-million-five-hundred-thousand-dollars-seed).

**Comparative question for this file (as briefed):** Cognee keeps the document structure and a graph over
it; Strata flattens documents into verbatim sentences on a shelf and throws away the spine. For a store
whose documents are lore bibles, specs and policy papers (575 rows on one shelf today), what does a
document spine buy — retrieval precision, passage expansion for the model to cite, or a clean unit for a
KV cartridge prefix?

**The short answer, up front (§"What Cognee does better" #1 and the central judgment at the end of §3):
precision, no; citation, yes and cheaply; cartridge unit, yes.** Cognee's retrieval quality does **not**
come from the spine — it comes from chunk *size* (default ~8,191 tokens, so a hit is already a passage)
plus a second summary vector lane plus an entity lane. What the spine actually does in Cognee's code is
exactly two things: render a deterministic, LLM-free citation
(`cognee/modules/retrieval/utils/references.py:277-360`), and make deletion safe
(`cognee/tasks/storage/chunk_ownership.py`). The load-bearing mechanism is not the graph and not a
section hierarchy — **they have neither** — it is the **tiling invariant**: stored chunks concatenated in
`chunk_index` order equal the document text byte-for-byte, enforced on every write
(`cognee/modules/chunking/incremental_chunking.py:53-66, 309-321`). Strata's `m2_ingest.py` already
stores sentences verbatim with `{file, n}` provenance, so we are one dense-gap-free `seq` column and one
assertion away from the same invariant. That is the portable part.

---

## What it is

Cognee is a **graph-native data-to-memory engine**, not a fact store. Its own one-liner: "Cognee is the
open-source AI memory platform for agents… Turn documents, code, and conversations into a self-hosted
knowledge graph your agents can search and reuse" (`README.md`). Four public operations
(`cognee/api/v1/{remember,recall,improve,forget}/`):

| op | what it does |
|---|---|
| `remember` | `add()` → `cognify()` → `improve()`; documents, folders, URLs, repos, DB rows, code |
| `recall` | pick a search strategy (20 of them) and retrieve context or an answer |
| `improve` | enrich memory, apply feedback, bridge session knowledge into the graph |
| `forget` | delete one document, a dataset, or only the derived graph+vector memory |

Three stores, deliberately overlapping, each with a documented role
(https://docs.cognee.ai/core-concepts/architecture):

* **relational** — documents, chunks, provenance, permissions, pipeline state (`Data` rows:
  `cognee/modules/data/models/Data.py`);
* **vector** — one collection per indexed field per DataPoint type, named `<TypeName>_<field>`
  (`cognee/infrastructure/engine/models/DataPoint.py:109-132`);
* **graph** — entities and relationships, plus the structural skeleton
  (`Document -[is_part_of]<- DocumentChunk -[contains]-> Entity`).

Defaults are all embedded: **SQLite + LanceDB + Kuzu/Ladybug** (`.env.template:154` `DB_PROVIDER="sqlite"`,
`:242` `VECTOR_DB_PROVIDER="lancedb"`; adapters under
`cognee/infrastructure/databases/{graph/{kuzu,ladybug},vector/lancedb}`). An independent comparison notes
the embedded defaults let a team "start with no external infrastructure", and that **Ladybug succeeds
Kuzu** as the default graph store
(https://mnemoverse.com/docs/library/ai-memory-solutions-2026-q3; fork note:
https://thedataquarry.com/blog/from-kuzu-to-ladybug/).

**What it is not, for our purposes:** it is not a per-fact memory. The atomic unit is a *document chunk*,
and the retrieval unit is a chunk. Facts (entities + relationship edges) are a *derived* second view, and
producing them costs two LLM calls per chunk (§Local operation & cost).

**A note on the moving target.** The API surface was renamed around v1.0 (`add`/`cognify` became legacy
operations under `remember`/`recall`; `docs.cognee.ai/core-concepts/main-operations/legacy-operations/`).
Third-party pages written in mid-2026 still describe the old surface, and one comparison page carried two
corrections about Cognee in six weeks
(https://mnemoverse.com/docs/library/ai-memory-solutions-2026-q3 — "the Q3 edition said Cognee has no BM25
or dedicated temporal retrieval… this edition replaces them"). Read any external Cognee claim against a
dated commit.

---

## The ECL pipeline

The task list is literally commented Extract / Cognify / Load, `cognee/api/v1/cognify/cognify.py:597-635`:

```python
tasks = [
    # EXTRACT: classify raw Data items into typed Document objects
    Task(classify_documents, needs_llm=False),
    # EXTRACT: split Documents into semantic text chunks
    Task(extract_chunks_from_documents, max_chunk_size=max_chunk_size, chunker=chunker, needs_llm=False),
    # COGNIFY: extract entities and relationships into a knowledge graph and
    # summarize each chunk for retrieval with the LLM.
    Task(extract_graph_and_summarize, graph_model=graph_model, ...),
    # LOAD: persist nodes, edges, and embeddings to graph/vector DBs
    Task(add_data_points, embed_triplets=embed_triplets, ..., needs_llm=False),
]
if track_provenance:      tasks.append(Task(record_provenance, ...))       # opt-in, default OFF
if check_contradictions:  tasks.append(Task(detect_contradictions, ...))   # opt-in, default OFF
if functional_relationships: tasks.append(Task(resolve_temporal_contradictions, ...))  # opt-in
```

`needs_llm=False` marks the deterministic tasks; "the run's need is the union over the tasks, so the LLM
connection probe runs exactly when some task in the list will use it" (same file, comment at `:598-601`).
That is the same discipline as our opt-in/byte-identical rule, implemented as a task attribute rather
than a config flag.

**Stage by stage, with the code:**

1. **`add`** — content-addressed storage write + a `Data` row. Dedup is a *lookup, not an identity*:
   `Index("data_dataset_content_lookup", "dataset_id", "owner_id", "content_hash")` — "adding content
   already present in a dataset (by the same owner/tenant) reuses that row via this index; the id itself
   carries no content information and stays stable when the document's content changes"
   (`cognee/modules/data/models/Data.py:12-31`). Rows are **dataset-scoped**: "the same content in two
   datasets is two rows with two ids, so updating one document can never touch another dataset's data"
   (`:45-51`).
2. **`classify_documents`** — extension → typed Document class
   (`cognee/tasks/documents/classify_documents.py:24-49`: pdf→`PdfDocument`, txt/md/json/xml/yaml→
   `TextDocument`, csv→`CsvDocument`, docx/xlsx/pptx→`UnstructuredDocument`, images/audio→
   `ImageDocument`/`AudioDocument`). **The Document graph node's id is set to the `Data` row id**
   (`classify_documents.py:166` `id=data_item.id`), which is what makes a citation resolvable to an
   ingested file (`cognee/modules/retrieval/utils/references.py:344-349`: "document_id == the ingested
   Data item's id (cognify sets Document.id = data.id), i.e. the dataId a caller needs to map a citation
   back to the document they ingested").
3. **`extract_chunks_from_documents`** — runs the configured chunker over the document's text stream
   (`cognee/tasks/documents/extract_chunks_from_documents.py`), accumulates `document_token_count`,
   writes it back to `Data.token_count`, and attaches per-chunk date hints computed **over the whole
   document in chunk order** (`:60-72`, `hint_lines`).
4. **`extract_graph_and_summarize`** — the only LLM stage: one structured-extraction call per chunk into
   a `KnowledgeGraph` pydantic model, plus one summarization call per chunk → `TextSummary` with
   `made_from: DocumentChunk` (`cognee/tasks/summarization/models.py:22-38`).
5. **`add_data_points`** — walks the DataPoint tree, writes nodes+edges to the graph and embeds every
   `index_fields` entry to the vector store (`cognee/tasks/storage/add_data_points.py`,
   `index_data_points.py`, `index_graph_edges.py`).
6. **opt-ins** — `record_provenance` (append-only lineage ledger, `cognee/tasks/provenance/record_provenance.py`),
   `detect_contradictions` (LLM-judged, writes `contradicts` edges,
   `cognee/tasks/graph/detect_contradictions.py`), `resolve_temporal_contradictions` (deterministic
   supersession of *declared* single-valued relationships,
   `cognee/tasks/graph/resolve_temporal_contradictions.py`).

**Alternative pipelines in the same seam** (`get_default_tasks` is not the only one):
`get_dlt_tasks` — "No LLM tasks: each manifest row becomes one DocumentChunk (vector-indexed by
add_data_points) and the graph structure comes from the relational schema via extract_dlt_source_edges"
(`cognify.py:663-701`); and the GLiNER demo list
`classify_documents → prepare_gliner_schema → extract_chunks_from_documents →
extract_graph_and_summarize_with_gliner → add_data_points`
(`cognee/tasks/graph/gliner_demo/tasks.py:3-5`). There is also a temporal variant list
(`cognify.py:734-748`) that swaps in `extract_events_and_timestamps` + `extract_knowledge_graph_from_events`.

**The DataPoint graph model.** `DataPoint` is the base class of every graph node
(`cognee/infrastructure/engine/models/DataPoint.py:28-92`). The interesting part is the storage contract
in the `metadata` dict, which is the closest thing Cognee has to our schema discipline:

* `index_fields` — fields to embed; each gets its own vector collection `<TypeName>_<field>`;
* `identity_fields` — fields whose values derive the node id as `uuid5`, "so the same real-world thing
  maps to the same node on every ingestion and graph writes merge. A subclass without them gets a random
  id and **can never be merged**" (`:41-45`);
* `transparent` — the node is unwritten and its edges re-attach to its children.

Plus the shared columns every node carries: `valid_to` ("Bi-temporal validity: ms epoch when this fact
was superseded (via close_node); None = still current"), `ontology_valid`, `ontology_uri`, `version`,
`topological_rank`, `belongs_to_set`, `source_pipeline`/`source_task`/`source_node_set`/`source_user`/
`source_content_hash`, `feedback_weight`, `importance_weight` (`:80-92`).

Entity identity is name-derived: `Entity.metadata = {"index_fields": ["name"], "identity_fields": ["name"]}`
(`cognee/modules/engine/models/Entity.py:22`), normalized "lower-case, spaces→_, strip apostrophes"
(`DataPoint._normalize_identity_value`, `:189-200`). Consequence, stated in their docs: "every mention of
the same name — across chunks, across documents, and across later cognify runs — resolves to the same
Entity node instead of creating a duplicate"
(https://docs.cognee.ai/core-concepts/main-operations/cognify). This single fact is why their
cross-ingestion contradiction detection and supersession work at all
(`detect_contradictions.py:20-24`, `resolve_temporal_contradictions.py:12-16`).

---

## Document ingestion & chunk provenance (the key section)

### The chunk record — every field, with its stated reason

`cognee/modules/chunking/models/DocumentChunk.py:36-67`:

```python
text: str
chunk_size: int
chunk_index: int
cut_type: str
content_hash: str | None = None       # "Hex digest of `text` … the chunk id is derived from it, so
                                      #  identity survives edits that shift chunk positions."
max_chunk_tokens: int | None = None   # "Incremental updates re-chunk a region with the budget
                                      #  recorded on the chunks it replaces, so a document stays
                                      #  self-consistent even when the global configuration changes."
chunker_id: str | None = None         # "Chunkers disagree on boundaries — an overlapping chunker's
                                      #  output cannot tile its input — so a document may only be
                                      #  updated by the chunker that built it."
is_part_of: Document                  # <- the spine edge
contains: list[Entity | Event | Timestamp | tuple[Edge, ...]] = None
importance_weight: float | None = 0.5
document_id: str | None = None        # flat scalars, duplicated onto the chunk
document_name: str | None = None      # "for reference rendering"
external_metadata: str | None = None  # parent document's JSON metadata, copied onto EVERY chunk
metadata: dict = {"index_fields": ["text"]}
```

`external_metadata` is copied by a model validator so no construction site can forget it
(`:89-102`), and is kept as **JSON text, not a dict**, because "LanceDB, the default vector store, cannot
map a dict field onto an Arrow type … One representation across every backend"
(`cognee/modules/chunking/external_metadata.py:1-15`). This is the same trap as our own Arrow/JSON
decisions — worth knowing a well-funded project hit it identically.

Private, never-persisted attrs on the chunk: `_produced_edge_identities` (the edges *this chunk's*
extraction yielded, needed because "the entities' `relations` lists … accumulate edges from every chunk
in a batch"), `_provenance_edges`, `_temporal_hints` (`:69-87`).

### What the spine actually is — and what it is not

**It is a two-level spine: Document → ordered chunks.** Verified by exhaustive grep: there is **no
section, heading, outline, page-number, or stored character-offset field anywhere in the ingestion or
chunking code**. Searching `outline|section_|heading|page_number|start_char|end_char|offset` across
`cognee/modules/chunking`, `cognee/modules/data/processing`, `cognee/tasks/documents`,
`cognee/tasks/ingestion` returns **only** the diff/tiling arithmetic inside
`incremental_chunking.py` — no persistence of any of it.

* A PDF is read page-by-page and the page number is **discarded**: `for page in file.pages: yield
  page_text` (`cognee/modules/data/processing/document_types/PdfDocument.py:18-22`).
* An Office document is flattened through `unstructured.partition` and re-joined blind:
  `"\n\n".join([str(el) for el in elements])`, then read in 1 KB blocks whose stop condition was a
  whitespace bug (`cognee/modules/data/processing/document_types/UnstructuredDocument.py:24-33`).
* A text document streams 1 MB blocks; the comment on the EOF check is a tiling confession: "Stopping on
  a whitespace-only block (the old `not text.strip()` check) **silently dropped everything after it** —
  a read block that happens to land inside a long whitespace run is real content, and **downstream
  offset/tiling checks need it**" (`TextDocument.py:14-20`).

**Offsets are derived, not stored.** `chunk_offsets(old_text, old_chunks)` walks the stored chunk texts
and asserts each one starts at the running cursor: "the chunks must tile it", raising
`IncrementalPlanError("stored chunk {i} does not tile the stored document text")` and
"stored chunks do not cover the stored document text" (`incremental_chunking.py:53-66`). Everything
positional in Cognee — the diff, the region plan, the citation number — hangs off that.

The chunker is built for it. `chunk_by_paragraph` advertises "a chunking strategy that enables **exact
text reconstruction**" and emits `cut_type` recording *how* each boundary was cut
(`paragraph_end` / `sentence_cut` / `default`) (`cognee/tasks/chunks/chunk_by_paragraph.py:9-31`,
`chunk_by_sentence` underneath). Chunk size is measured in **tokens of the embedding model's tokenizer**,
not characters.

**Chunk identity is content, not position** — `cognee/modules/chunking/chunk_id.py:1-22`:

```python
"""Content-derived chunk identity.
Chunk ids were previously positional (uuid5(doc_id-chunk_index)), which made every id downstream of an
edit shift or collide once chunk indices moved. Identity is now derived from the chunk's content hash
instead: an unchanged chunk keeps its id no matter where it sits, and re-ingesting identical content is
idempotent. The occurrence counter disambiguates identical texts appearing more than once in the same
document (two equal paragraphs stay two nodes)."""
def content_chunk_id(document_id, content_hash, occurrence):
    return uuid5(NAMESPACE_OID, f"{document_id}:{content_hash}:{occurrence}")
```

`TextChunker` maintains `hash_occurrences: dict` per document and a `chunk_identity()` closure
(`TextChunker.py:20-28`). **This is a design they had to migrate to** — positional ids were the original
and broke. Our rows have the same latent problem in a milder form: `source=ingest-<date>` + `{file, n}`
where `n` is positional, so re-ingesting a file with an inserted sentence renumbers every later `n`.

**Chunker identity is stamped so incompatibility is *reported* rather than surfacing as a tiling
failure**: "Chunkers disagree on boundaries — LangchainChunker overlaps consecutive chunks, so its output
cannot tile its input — and a document can only be incrementally updated by the chunker that built it.
Recording the identity is what lets that be *reported* … instead of surfacing as an indistinguishable
tiling failure" (`cognee/modules/chunking/Chunker.py:3-10`).

### Chunk → document linkage in storage, and how retrieval uses it

Three copies, deliberately redundant:

1. **Graph:** `DocumentChunk -[is_part_of]-> Document`, plus `Document -[belongs_to_set]-> NodeSet`.
   The edge's ownership is exact: "`chunk -[is_part_of]-> document` keeps its chunk owner: that edge
   exists only because the chunk does, and must die with it", while `document -[belongs_to_set]-> NodeSet`
   is document-scoped and "outlives every chunk" (`cognee/tasks/storage/chunk_ownership.py:193-199`).
2. **Vector payload:** the index schema carries flat scalars so a hit is self-sufficient —
   `document_id`, `document_name`, `chunk_index`, `source_chunk_id`, `importance_weight`,
   `external_metadata`, `belongs_to_set` (`cognee/infrastructure/databases/vector/lancedb/LanceDBAdapter.py:85-101`;
   identical fields in `pgvector/PGVectorAdapter.py:52-62` and `turso/TursoVectorAdapter.py:56-66`,
   populated at `:1696-1697`, `:506-507`, `:387-388`). Comment: "Optional reference scalars carried for
   the search 'Evidence' feature."
3. **Relational:** the `provenance_entries` ledger, opt-in, "document → chunk → entity → relationship
   lineage entries … All entries for one task invocation are committed as ONE chained transaction"
   (`cognee/tasks/provenance/record_provenance.py:1-30`). Keys are dataset-prefixed
   (`"{dataset_id}:{raw_id}"`) because "cognee entity ids are globally deterministic (name-derived
   uuid5s), and the ledger lives in the always-shared relational DB — without this, two tenants
   mentioning the same entity name would share (and cross-overwrite) one version chain."

**How retrieval uses the spine — exactly two uses, and neither is expansion:**

* **Citation.** `format_chunk_references` reads `payload["document_name"]`, `payload["chunk_number"]`
  (falling back to `chunk_index + 1`), and `payload["text"]`; "Document name and a chunk number are both
  required to ground the citation; text is required for a meaningful snippet" — an entry missing any of
  the three is **skipped, not degraded** (`references.py:335-343`, `:200-219`). Output is an
  `Evidence:` block of 3–5 bullets with 160-char snippets (`:31-38`). The module's docstring is the
  design statement: "Evidence is grounded in the **generated answer**, not in whatever happened to be
  retrieved before the LLM was called … All helpers are pure with respect to the LLM (no model calls) so
  they can be unit tested in isolation" (`:1-16`). Candidates that share no significant term with the
  answer (stopword-filtered) are dropped and the rest re-ranked by term overlap; an answer with no
  significant terms "cannot be grounded and yields `""`" rather than presenting retrieval order as
  provenance (`:288-291`, `:318-324`).
* **Deletion.** Chunk-scoped ownership: "A chunk owns EXACTLY what its own extraction produced … Output
  reachable from several chunks gets several owners; output no chunk produced — the document node,
  NodeSet tags, ontology enrichment — stays document-scoped. Exactness is what chunk-scoped deletion
  relies on: an artifact is hard-deleted when its last owner dies and kept while any owner lives"
  (`chunk_ownership.py:1-25`). And the failure they fixed is our failure mode in embryo: "Relationship
  edges hang off their SOURCE entity (`Entity.relations`), so a walk that followed them would hand a
  chunk everything downstream of any entity it mentions — a chunk containing only `hole` owned the whole
  `hole -> rabbit -> alice -> queen` chain, and deleting the chunk that contained `queen` left it alive
  as a ghost."

**The spine is explicitly *not* a retrieval signal.** Their own contradiction detector hard-codes the
structural edges out of fact comparison: `STRUCTURAL_RELATIONSHIPS = {"contains", "is_part_of",
"made_from", "exists_in", "contradicts"}` (`cognee/tasks/graph/detect_contradictions.py:38-40`). The
visualizer groups them separately from semantic edges
(`cognee/modules/visualization/preprocessor.py:101, 1058-1077`). And the hybrid prompt renders passages,
entities and facts as three flat sections — `## Global context` / `## Relevant passages` / `## Relevant
entities` / facts — with `---` between passages and **no document grouping, no adjacency, no hierarchy**
(`cognee/modules/retrieval/hybrid/context.py:9-27, 74-84`).

**No hit is ever expanded.** Grepping the whole retrieval tree for adjacency/neighbor-chunk logic finds
nothing (`chunk_index` appears only to compute a display number, and `adjacent` in `code_retriever.py:473`
is code-graph traversal). `CHUNKS` returns raw payloads of the chunks themselves
(`cognee/modules/retrieval/chunks_retriever.py:55-80`). `GRAPH_COMPLETION_CONTEXT_EXTENSION` grows
context by **more vector searches over triplets over 4 rounds** (`graph_completion_context_extension_retriever.py:31`),
not by walking the document. **They do not need expansion because their chunks are ~8k tokens: a hit is
already a passage.** That is the whole difference with Strata, and it is a *sizing* difference, not a
*structure* difference.

### Ingestion of documents, mechanically

`max_chunk_size` default = `min(embedding model max tokens, LLM max completion tokens / 2)` ≈ **8,191
tokens** with default OpenAI settings (`.agents/skills/cognee-performance/SKILL.md:34`, and
`cognee/infrastructure/llm/utils.py:resolve_chunk_size`). Their own documented pitfall: "**Big default
chunks with a small local embedder.** The default chunk size follows the configured embedding limit
(8191), **even for fastembed's 512-token model**. Pass a smaller `chunk_size` for local embedding models"
(`cognee-performance/SKILL.md:150-154`). Read that as: with our embedder (bge-small, 512 tokens) their
default would silently over-run our model's input window.

Also documented as dead config: "**`CHUNK_SIZE`, `CHUNK_OVERLAP`, `CHUNK_STRATEGY` (in `.env.template`)
and `cognee.config.set_chunk_size()` etc. have no effect on ingestion.** Pass `chunk_size=` to
`remember()` instead" (`cognee-performance/SKILL.md:139-141`). Env knobs that don't bind — same class of
bug we guard against with the byte-identical default rule.

Chunkers available: `TextChunker` (`text_chunker_v1`), `TextChunkerWithOverlap`
(`text_chunker_overlap_v1`, ratio-based), `CsvChunker`, `JsonListChunker`, `LangchainChunker`
(`cognee/modules/chunking/`). Overlap exists but is opt-in and, by their own comment, breaks tiling.

---

## Ontology & categorization (compare to our LCC shelves)

Cognee has **three** distinct categorization mechanisms. Keeping them separate is what makes the
comparison to our LCC shelves useful.

### A. Ontology grounding — a *canonicalizing* vocabulary, matched with `difflib`

Docs: https://docs.cognee.ai/guides/ontology-support,
https://docs.cognee.ai/core-concepts/further-concepts/ontologies. Code:
`cognee/modules/ontology/` (1,433 LOC).

What grounding actually reads is narrow and stated: "Grounding reads only `owl:Class`, `rdf:type`,
`rdfs:subClassOf`, and `owl:ObjectProperty` — so an ontology can supply entity **types** and
**relationships** between its own terms, but not node properties (`owl:DatatypeProperty` is ignored)."
It then (i) renames matched nodes to the canonical term and sets `ontology_valid=True`, and (ii) extends
the graph with the matched term's **parent classes and object-property links** (a BFS closure,
`RDFLibOntologyResolver.get_subgraph`, `cognee/modules/ontology/rdf_xml/RDFLibOntologyResolver.py:274-300+`).

**The matcher is not an embedding and not an LLM.** `find_closest_match` normalizes
(`name.lower().replace(" ", "_")`) and delegates to a pluggable strategy; the default is
`FuzzyMatchingStrategy(cutoff=0.8)` = exact-settle-then-`difflib.get_close_matches(n=1, cutoff=0.8)`
(`cognee/modules/ontology/matching_strategies.py:20-51`,
`RDFLibOntologyResolver.py:256-264`). Their own marketing diagram says the same: "Match each entity type
→ OWL Class (fuzzy, 80% cutoff)" (https://www.cognee.ai/grounding-ai-memory).

Two modes, and the default is the one we should copy:

* **`annotate` (default)** — "unmatched entities are kept as-is, never rejected."
* **`strict`** — "a node survives only if the ontology matched either its type against a class or its
  name against an individual, and edges touching a dropped node go with it," gated on
  `EmptyOntologyInStrictModeError` ("a mistyped path … raises rather than quietly building an empty
  graph"), and **instrumented**: "read the aggregate dropped/retained warning Cognee logs (one per chunk
  batch)" — `Strict ontology mode dropped %s of %s extracted node(s) (%.0f%% retained)`
  (`cognee/modules/ontology/construct_data_points_and_edges_with_ontology.py:245-252`, counters at
  `:42-50`). Their advice: "**A high drop ratio is the signal that the ontology does not cover your
  corpus's vocabulary** … check that summary on a small run before enabling it over a large corpus."
* And crucially, **grounding never touches text**: "Chunk text is unaffected either way: it is stored and
  embedded before grounding runs, so `CHUNKS` and `RAG_COMPLETION` still return passages mentioning
  dropped entities."

Ontology files are parsed **once at construction** — "There is no automatic file system monitoring, so
changes to the `.owl` file on disk are not picked up while a session or server is running."

**Direct read onto our LCC shelves.** Our keyword map (2-token margin, else `wild` at 18%) is Cognee's
`annotate` mode with three differences: (a) ours assigns **one** shelf per row, theirs annotates a *type*
and a *name* independently and keeps both; (b) ours has **no canonicalization** — two spellings of the
same shelf word produce two shelves, whereas they collapse matches to the ontology's canonical term and
record `ontology_uri` on the node "so the persisted graph keeps the external identifier instead of
collapsing it to a local label" (`DataPoint.py:72-77`); (c) ours has **no drop-ratio instrumentation** —
18% `wild` is a number we computed offline, not a per-batch log line they surface at ingest. All three
are cheap to fix and all three are measurement, not inference. Note also that their ontology grounds
*entity types*, not *documents* — the closest analog to our per-file `--class` is not the ontology at all,
it is presort (below).

### B. NodeSets — flat tags, filtered at query time, no router

`NodeSet` is a named group; nodes carry `belongs_to_set`. Identity is `generate_node_id("NodeSet:<name>")`
and "Two spellings that normalize alike (case, spaces, apostrophes) are one node set"
(`cognee/modules/engine/models/node_set.py:11-32`) — **note they solve the aliasing problem our keyword
map has, by normalizing the tag itself.** `index_fields` is deliberately empty: "node-set names are
filters, not embedded content."

At query time: `node_name=["AI"]` + `node_name_filter_operator="OR"|"AND"`
(`cognee/modules/search/methods/search.py:111-112`), pushed into the vector search as a payload filter
(`cognee/modules/retrieval/hybrid/chunks.py:182-210`) and re-checked on attached rows
(`:213-249`, `payload_matches_node_filter`). It is **caller-supplied, not inferred**: nothing in Cognee
picks a NodeSet from the query text. And its coverage is patchy: "It has no effect on `SUMMARIES`,
`CYPHER`, or `NATURAL_LANGUAGE`" and "SUMMARIES, CHUNKS_LEXICAL, GRAPH_REPORT, CYPHER, NATURAL_LANGUAGE,
CODE and SKILLS ignore it" (https://docs.cognee.ai/core-concepts/further-concepts/node-sets;
`.agents/skills/cognee-recall/SKILL.md:95-98`).

**Read onto us:** our `scope` + `class` filters are the same idea and *better* covered (they apply to
every arm of our recall path, whereas Cognee's tag filter silently does nothing for 7 of 20 search types
— a trap for our `memory_search` tool surface: if we expose a `class=` filter, it must mean the same
thing everywhere or we must say where it doesn't).

### C. Presort — a *pre-ingestion* file→dataset classifier (their real analog of `--class`)

`cognee/tasks/presort/` + `cognee/modules/presort/`. Deterministic layers in priority order
(`cognee/tasks/presort/group_files.py:1-16`):

1. **code projects** — a subdirectory with a project marker (`pyproject.toml`, `.git`) becomes one group,
   kind `code_project`;
2. **folder structure** — remaining subdirs become **dot-namespaced groups** (`a.b.c`), kind `folder`;
3. **loose files in the root** — grouped by **extension family**, kind `extension_family`;
4. opt-in **`use_llm=True`**: "loose text documents are embedded (name + content class) and clustered
   with a union-find over cosine similarity … producing `semantic` groups that replace their
   extension-family grouping." Thresholds: `_SEMANTIC_SIMILARITY_THRESHOLD = 0.6`,
   `_MIN_SEMANTIC_GROUP_SIZE = 2`.

It also flags junk, duplicates (hash clustering, keeps the first path) and PII, and applying is
idempotent: re-applying "is idempotent: content the report marked `cognified` is skipped by the pipeline"
(`cognee/modules/presort/apply_presort.py:1-16`, `:29-40`).

**Read onto us:** this is the mechanism we don't have for *writing* shelves. Our `--class` for a whole
file is manual; Cognee's layer 2 (dot-namespaced directory path → group name) is a zero-inference
classifier that would map our lore-bible/spec/policy directory layout onto shelves directly, and layer 4
is exactly the "embedding + union-find" step we already have the vectors for. The `wild` 18% is precisely
their "loose files" bucket.

### D. Global Context Index — a *hierarchical* learned index (the answer to "shelves too coarse")

Our stated problem: "95% of rows sit on shelves too coarse to navigate." Cognee's answer is not a better
taxonomy but a **bottom-up aggregation tree over summaries**
(https://docs.cognee.ai/core-concepts/further-concepts/global-context-index):

```
root GlobalContextSummary -> optional higher-level bucket -> GlobalContextSummary bucket
                          -> TextSummary -> DocumentChunk
```

"The **build** process is bottom-up, starting from `TextSummary` nodes. The **retrieval** hierarchy is
top-down, starting from the root summary. The index groups `TextSummary` nodes, not raw `DocumentChunk`
nodes directly. Two search types read the index: `GRAPH_COMPLETION` and `HYBRID_COMPLETION`."

Code: `cognee/tasks/memify/global_context_index/{build,update,persist,summarize,load,ids,models}.py`;
`GlobalContextSummary(text, dataset_id, level, is_root, graph_bucket_entity_ids, summarized_in)`
(`cognee/tasks/summarization/models.py:8-19`); collection `GlobalContextSummary_text`
(`constants.py`). Bucketing has **two strategies** — `Literal["vector", "graph"]`
(`bucketing_strategy.py:5`) — with knobs `max_bucket_size` (min 2), `placement_distance_threshold`,
`min_overlap` (`update.py:38-61`). Retrieval reads it as a *prelude*: root text + top-k non-root buckets
injected as a `## Global context` section above the passages
(`cognee/modules/retrieval/utils/global_context.py:12-56`, `hybrid_retriever.py:263-279`). The root is
skipped in the top-k list by `limit=top_k + 1` "in case the root is in the top results" (`:38-40`).

This is built by `memify`/`improve`, i.e. **offline enrichment, not on the write path** — which is the
posture our curation timer already has.

---

## Read path (vector/graph/hybrid)

### 20 search types, one table

`cognee/modules/search/types/SearchType.py` (enumerated verbatim) × the retriever map in
`cognee/modules/retrieval/` (20 retriever files + `hybrid/` + `temporal_hybrid/`). LLM cost per type,
from `.agents/skills/cognee-performance/SKILL.md:87-104` and
`.agents/skills/cognee-recall/SKILL.md:52-71`:

| no-LLM types | LLM types |
|---|---|
| `CHUNKS` (vector), `CHUNKS_LEXICAL` (BM25), `SUMMARIES`, `CODE`, `SKILLS`, `CYPHER`, `GRAPH_REPORT` (partly) | `HYBRID_COMPLETION` (1), `GRAPH_COMPLETION` (1), `RAG_COMPLETION` (1), `TRIPLET_COMPLETION` (1), `CODING_RULES`, `AGENTIC_COMPLETION` (loop), `NATURAL_LANGUAGE` (1 + up to 3 retries), `FEELING_LUCKY` (+1 to pick the type), `TEMPORAL` / `GRAPH_COMPLETION_COT` / `_DECOMPOSITION` / `_CONTEXT_EXTENSION` / `_GRAPH_SUMMARY_COMPLETION` ("several") |

### Hybrid = **three lanes**, two of them vector, fused by RRF

`cognee/modules/retrieval/hybrid_retriever.py` (374 lines) + `hybrid/` (1,442 lines).

```python
chunk_objects, (entities, fact_candidates) = await asyncio.gather(
    retrieve_hybrid_chunks(...),        # lane 1
    self._retrieve_entities_and_facts(...),  # lanes 2 + 3
)
```
(`hybrid_retriever.py:171-196`; "run concurrently with the chunk lane so the graph round trip for edge
bullets overlaps the chunk pipeline's ranking and summary loading", `:199-205`).

* **Lane 1 — chunks, itself two vector collections.** `DocumentChunk_text` (+ `DltRow_text` when
  present) and `TextSummary_text`, fetched in one `gather` (`hybrid/chunks.py:58-75`), paired 1:1 by
  `summary_id_for_chunk`, missing chunks re-loaded by id (`:213-249`), then RRF-fused. Candidate
  oversampling is explicit: `chunk_candidate_limit = chunks_top_k * 2`,
  `summary_candidate_limit = chunks_top_k` (`:120-127`).
* **Lane 2 — entities:** vector top-k over `Entity_name` collections, then a **graph** round trip to pull
  each entity's edges (`hybrid/entities.py`, `build_entities(... max_edges_per_entity=10 ...)`).
* **Lane 3 — facts:** a **third vector collection over edge text**. Edges are embedded as `EdgeType`
  DataPoints keyed on the relationship's *retrieval text*
  (`cognee/tasks/storage/index_graph_edges.py:32-52`; `cognee/modules/graph/models/EdgeType.py` —
  `identity_fields: ["relationship_name"]`). Facts are selected against the entities actually shown, so
  "a fact is dropped as 'already shown under entity X' only when X is shown"
  (`hybrid/candidates.py:1-13`), and facts with < 3 words are dropped (`hybrid/facts.py:9-12`). Facts
  are excluded from used-id tracking because "Facts are EdgeType vector rows, not graph nodes"
  (`hybrid/context.py:37`).

**So: vector + graph + vector-over-edges. There is no BM25 in the hybrid path.** Lexical BM25 is a
*separate search type* (`CHUNKS_LEXICAL`), and the router can only reach it when the whole query is one
quoted string (§below). That is a materially different design from our RRF of bm25+vector, and it is
weaker in one specific way: **their lexical arm is not fused, and their fusion has no lexical channel at
all.**

### The fusion — and its k is adaptive

`cognee/modules/retrieval/hybrid/ranking.py:26-52`:

```python
rrf_k = _rrf_k(limit)
rrf_score = sum(1.0 / (rrf_k + rank + 1) for rank in ranks)   # ranks over (vector_rank, summary_rank)
final_score = rrf_score
if use_importance_weight: final_score *= _importance_factor(chunk)     # 0.75 + 0.5 * importance
if use_truth_weight ...:  final_score *= truth_factor(...)             # opt-in
if personal_weights ...:  final_score *= personal_factor(..., distance_space=False)
ranked.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))   # score, rrf, best raw rank, id
def _rrf_k(chunks_top_k): return max(30, min(60, 20 + 2 * chunks_top_k))
```

Three things to steal: **k scales with the cut** (30–60, so at `top_k=5` their k is 30, not our fixed
60); every extra signal is a **multiplicative factor bounded to ±25%** (`0.75 + 0.5·w`, `w∈[0,1]`) so no
side-signal can flip the order by more than a quarter — that is a *bounded* demotion, exactly the shape
we decided to test after the `knowledge-update` −0.139 regression; and the tie-break is
`(final, rrf, min(raw_rank), id)` — deterministic and reproducible down to the id.

Empty channels are not errors: `except CollectionNotFoundError: logger.debug("… using empty channel")`
(`hybrid/chunks.py:205-210`), and an empty `TextSummary_text` row is "optional enrichment: DLT rows and
custom pipelines write none."

### Cross-channel merge, and a conversational reserve

`merge_hybrid_results` merges **each lane separately under its own budget**, rebuilds `chunk_summaries`
"for the chunks that survived so it never references a dropped chunk," and uses
`conversational_reserve(limit)` to hold back part of each budget for the secondary arm
(`cognee/modules/retrieval/hybrid/merge.py:1-61`, `utils/merge_results.py`). This is how a routed
second retrieval gets combined with the first without one arm eating the other's slots — relevant to us
because our own A/B arms currently don't merge at all.

### Deferred rerank without re-cutting

`HybridCandidates` is a frozen value object carrying the **uncut** fetch; `prioritize()` is a "stable
partition: nothing is removed or altered, and the fetch order is kept among the unnamed"; `extend()`
appends new chunks without reordering; **`finalize()` is "the only place a candidate list is cut"**
(`cognee/modules/retrieval/hybrid/candidates.py:1-13, 34-77`). The temporal rerank reorders then cuts;
plain hybrid cuts immediately. **This is the seam our reranker lacks**: our reranker +0.040 is a separate
stage; theirs is a reordering hook that runs against the *same* candidate list, so a rerank can only
reorder, never re-query, and the budget is enforced in one place.

### Context assembly

`format_hybrid_context` → `## Global context` (opt-in prelude) → `## Relevant passages` (chunk texts,
`---`-separated, optional allowlisted metadata lines above each) → `## Relevant entities` (entity blocks
with edge bullets) → facts (`hybrid/context.py:9-34, 74-97`; `hybrid/external_metadata.py`).
`max_context_chars` is applied in the completion layer, not the retriever
(`hybrid_retriever.py:310-317`).

**Empty-context guard** — worth copying verbatim as a policy: `skip_completion_on_empty_context = True`
with the comment "Search is not an LLM gateway: when every channel comes back empty there is no answer to
give (SDK-270 / gh #3728) … Empty context must not reach the LLM: … the only possible output is a
phantom 'no context provided' deflection" (`hybrid_retriever.py:60-62, 288-296`). An empty graph raises
`NoDataError` instead of returning empty dicts, because "an empty graph is a state problem, not a query
miss" (`:119-128`).

### The router — deterministic, two rules, and it only routes *strategies*

`cognee/api/v1/recall/query_router.py` (128 lines, read in full):

* Design rule, stated: "Auto-routing may only pick a strategy that is **at least as good as the HYBRID
  default** on a default-built graph and **does not add LLM calls without an unambiguous signal**"
  (`:8-17`).
* `_RULES`, first match wins (`:84-107`): `^"[^"]+"$` → `CHUNKS_LEXICAL`; a coding-rules/standards/
  conventions phrase → `CODING_RULES`; a **time preposition immediately followed by an absolute date**
  → `TEMPORAL`. The date regex is obsessively engineered: `for` requires the year to *end* the phrase
  because "`for` takes a quantity as readily as a date (`for 2000 users`)" (`:61-70`); a bare `when`,
  `since monday`, `the 2019 report` and `Q4 2024` must not fire "because the retriever's interval
  extraction would spend an LLM call to find no window in those" (`:39-45`).
* Fallback `HYBRID_COMPLETION`; the decision carries the **rule name** that fired
  (`RouteDecision(search_type, rule)`, `:31-37, 110-128`) — i.e. routing is logged as a structured field.
* A pinned test asserts no query in their golden table matches two rules
  (`test_no_query_matches_two_rules`, `:76`).
* `CYPHER` is deliberately unroutable: "auto-routing would let `{"query": "MATCH (n) DETACH DELETE n"}`
  mutate the graph for anyone who can read it" (`:78-83`).
* Retry: a **routed** type (never a pinned one) that the backend rejects, or a routed
  `CHUNKS_LEXICAL`/`CODING_RULES` that returns nothing, is retried **once** as `HYBRID_COMPLETION`;
  "the retry only second-guesses a guess" (`cognee/api/v1/recall/recall.py:59, 647-671`).
* With no LLM key, routing is skipped entirely and `recall()` defaults to `CHUNKS`
  (`.agents/skills/cognee-recall/SKILL.md:27-31`).
* The LLM router (`select_search_type`, `cognee/modules/search/operations/select_search_type.py`) exists
  but is reachable **only** via the explicit `FEELING_LUCKY` type, and falls back to `RAG_COMPLETION` on
  any invalid answer or exception.

**Verdict for us:** Cognee routes **query → strategy**, not **query → shelf**, and it does so with three
regexes and a measured-safety rule. We have the opposite half: a **document → shelf** classifier
(keyword map) and **no query → anything router**. Their piece is the missing one, it is ~130 lines of
regex, and it is free at query time.

### Lexical: BM25 that does not scale

`CHUNKS_LEXICAL` → `BM25ChunksRetriever(k1=1.5, b=0.75, DEFAULT_STOP_WORDS)`
(`cognee/modules/retrieval/bm25_retriever.py:5-49`). The base is the problem:
`LexicalRetriever.initialize()` calls `graph_engine.get_filtered_graph_data([{"type": ["DocumentChunk"]}])`
— i.e. **pulls every chunk node out of the graph DB**, tokenizes all of them into a Python dict, computes
IDF + avgdl in memory, and then per query **loops over every chunk scoring each one** and `nlargest`
(`cognee/modules/retrieval/lexical_retriever.py:59-130`). "This keeps the in-memory model of
LexicalRetriever and adds no dependency or persistence" (`bm25_retriever.py:10-14`).

That is O(all chunks) per query with a cold full-store scan on first use, per process, per dataset.
**Our FTS5 `bm25` is a real persisted inverted index and beats them outright here.** Also note their own
docs: "BM25 orders the results but does not attach a score to them"
(https://docs.cognee.ai/core-concepts/main-operations/legacy-operations/search).

### Graph retrieval, and the triplet lane

`GRAPH_COMPLETION` and friends use `brute_force_triplet_search` over the graph
(`cognee/modules/retrieval/utils/brute_force_triplet_search.py`), with `wide_search_top_k=100`,
`triplet_distance_penalty=6.5`, `feedback_influence`, `neighborhood_depth`
(`graph_completion_context_extension_retriever.py:27-35`). Optional `TRIPLET_EMBEDDING=true` indexes
whole triplets into `Triplet_text` for `TRIPLET_COMPLETION`
(`.env.template:656`; docs "Prereq: Triplet embeddings must exist"). `CYPHER` runs the query text
verbatim through `graph_engine.query()` — read permission is the only gate, hence the routing ban above.

---

## Incremental updates

This is the most mature part of the codebase and the part with the clearest lessons for us.
`cognee/api/v1/update/incremental.py` (1,149 lines) — its module docstring is a six-step design doc
(`:1-45`), quoted in pieces:

1. **Permission:** requires write **and** delete, "because this path destroys graph state — the full
   fallback reaches the same destruction through `delete_data`, which requires it, so the two branches
   must not disagree."
2. **Read baseline:** OLD processed text from `Data.raw_data_location` + stored chunk nodes "ordered by
   their position in that text."
3. **Stage before publish:** new content runs "through the same loader machinery as ingestion … but the
   `Data` row is NOT touched. **Readers keep resolving the coherent old version.**"
4. **Plan** through a **swappable `ChunkPolicy`** — "the default: diff into disjoint changed regions,
   re-chunk each at the budget its replaced chunks recorded, keep everything between them untouched."
   The plan is **COMPLETE**: "the writer executes it without deciding anything"
   (`cognee/modules/chunking/chunk_policy.py:1-27`). Its four outputs are `fresh`, `reused`
   (byte-identical replacement chunks that "keep their node id and subgraph"), `kept_moves` (untouched
   chunks whose position moved), `deleted_ids` (`:54-67`). Then: "Its result is then re-derived here and
   compared to the new text **byte-for-byte: the policy checked its own arithmetic, and the orchestrator
   trusts none of it**." Only then is a pipeline run record created — "refused updates leave no
   run-record noise."
5. **Write:** "extract ONLY the fresh chunks, in bounded batches … retire replaced chunk ownership
   through the shared deletion planner, and renumber moved survivors." On the LLM extractor, **date
   context is part of "fresh"**: hints are "a function of the whole document in chunk order," so "a
   surviving chunk whose hints changed — it inferred a date from text that the edit touched — is retired
   and re-extracted like a fresh one. GLiNER never reads the hints, so under it nothing is re-dated."
6. **Publish** in one relational transaction: "content location, hashes, size, token count, and the
   processed stamp flip together. A crash anywhere before the publish leaves the row on the old content;
   the stored chunks then no longer tile the stored text, so **the next touch fails closed into a full
   rebuild (self-heal)**."

The diff itself, `cognee/modules/chunking/incremental_chunking.py:1-18`:

```
Given the old processed text, its stored chunk texts (in document order), and the new processed text,
compute the minimal update plan as a list of DISJOINT replacement regions:
  - a paragraph-anchored diff yields every changed hunk in near-linear time for ANY change shape,
  - each hunk is expanded to the boundaries of the old chunks it overlaps,
  - each hunk whose chunk ranges touch are merged into one region.
Chunks between regions are KEPT — never re-chunked, so their boundaries (and therefore their ids,
entities, and summaries) cannot drift.
validate_no_loss enforces the hard invariant: kept chunks and region chunk texts, interleaved in
document order, must reassemble into exactly the new text.
```

Complexity engineering: three layers keep the diff near-linear — line prefix/suffix trim ("a fast path
only … No correctness or complexity property depends on it"), a `SequenceMatcher` over **paragraph
units** ("near-unique in prose, so the popular-element blowup of a raw line diff cannot occur"), and a
line-level diff inside each hunk, then `_shrink_span` to character precision
(`:153-196, 199-220`). The popularity filter is explicit about why: "SequenceMatcher walks EVERY
occurrence of an element for every query … a raw line diff over a log file (or a novel's blank lines)
goes quadratic" (`:102-115`).

Refusals are **typed**, not free text (`RefusalReason`, `incremental.py:135-156`): `disabled`,
`custom_extraction_config`, `per_call_db_config`, `unsupported_backend`, `unsupported_chunker`,
`unsupported_metadata`, `no_baseline`, `chunks_not_tiling`, `unreadable_text` — "Every refusal used to
surface as one free-text message and one log line, so **a permanent misconfiguration (an incompatible
chunker, an unsupported backend) looked exactly like a first ingestion.** The reason is logged as a
structured field and returned in `UpdateResult.fallback`." Permission errors are **not** refusals: they
propagate.

Re-ingestion semantics elsewhere: identical content is a **no-op**; changed content raises
`DocumentUpdateRequiredError` and you must call `cognee.update(data_id=…, data=…, dataset_id=…)`, "which
re-extracts only the changed chunks and keeps the document's id"
(`.agents/skills/cognee-ingestion/SKILL.md:152-159`).

Update semantics for *facts* (as opposed to documents) are separate and mostly **opt-in**:

* `valid_to` + `close_node()` + `is_valid()` on every `DataPoint` — "When a fact changes (e.g. 'Alice
  works at X' → 'Alice works at Y'), call `close_node(old_node_id)` to mark the old node as no longer
  valid — **instead of deleting it** — and then write the replacement." Last-write-wins; backends without
  partial node updates "log a warning and return `False`" (`cognee/tasks/storage/close_node.py:1-46,
  49-56`). Shipped as PR #3789 "bi-temporal DataPoint support"
  (https://github.com/topoteretes/cognee/pull/3789), which states the gap plainly: "cognee's temporal
  stack records *when events happened* … but has no concept of a fact *ceasing to be valid* — today that
  exists only by delegating to Graphiti."
* `resolve_temporal_contradictions` — "Nothing is deleted: a superseded edge stays in the graph with its
  provenance, tagged (`superseded`, `superseded_by`, `supersession_reason`)". **Cardinality cannot be
  inferred, only declared**: "Cognee's LLM-extracted relationships are mostly many-valued (`knows`,
  `mentions`) and carry no cardinality metadata, so which ones hold a single target cannot be inferred —
  only declared" (`cognee/tasks/graph/resolve_temporal_contradictions.py:1-20`).
* `detect_contradictions` — LLM-judged, **off by default**, non-destructive ("it only adds edges, never
  rewrites/deletes"), and "swallows all of its own errors so contradiction detection can never break
  ingestion" (`cognee/tasks/graph/detect_contradictions.py:1-25`).

Independent read of the same machinery: "Zep marks the old edge invalid at a timestamp. Mem0 keeps both
and lets the reader model sort it out. **Cognee rebuilds structure in the graph**"
(https://particula.tech/blog/agent-memory-frameworks-tested-mem0-zep-letta-cognee-2026).

---

## Local operation & cost

### What runs with no cloud dependency

* **No LLM key at all:** "With no LLM key configured, Cognee extracts the graph with the local GLiNER
  model and embeds with a local embedding model; both download on first use" and "Text ingestion,
  retrieval, and session storage work without an LLM. **LLM-dependent improvement stages skip
  automatically**" (`README.md`). The Docker image "ships with the GLiNER runtime baked in, so text
  ingestion and retrieval work in Docker without an LLM key."
* **Embedder = ours.** "With no LLM key and no `EMBEDDING_*` settings, embeddings run on the local
  fastembed model (**`BAAI/bge-small-en-v1.5`**, a core dependency, ~67 MB download)"
  (`.env.template:713-715`; `DEFAULT_LOCAL_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"`,
  `cognee/infrastructure/databases/vector/embeddings/config.py:29`; "runs on the CPU and **blocks while
  it works**", `.agents/skills/cognee-performance/SKILL.md:77`). **Same model, same ONNX/CPU route, same
  384-dim as our pinned embedder.** Independent confirmation that our embedder choice is the industry
  default for CPU-only local memory, not a compromise.
* **Extractor = GLiNER2**, local, ~750 MB model, auto-installs a CPU torch on first use
  (`.agents/skills/cognee-ingestion/SKILL.md:123-133`; README news for v1.6.1). Inference concurrency is
  memory-shaped: "0 (default) sizes the concurrency to the machine: half of torch's thread count, capped
  so each in-flight batch (**~2 GB**) fits in available memory" (`.env.template:709-712`).
* **Stores:** SQLite + LanceDB + Kuzu/Ladybug, all embedded on local disk
  (`.env.template:154, 242`; `cognee/infrastructure/databases/{graph,vector}/`). Whole memory layer on
  one Postgres is available but "**released as a demo feature.** The production-ready version is
  available as a licensed product" (`README.md`).
* **Ollama / llama.cpp / LM Studio** are supported for generation
  (https://docs.cognee.ai/guides/local-setup, https://docs.cognee.ai/guides/local-ollama). Note for our
  protocol: on local providers "an unset `LLM_TEMPERATURE` sends `temperature: 0.0`, so extraction runs
  deterministically by default" — the opposite of our never-use-0 rule, and they call out that
  previously "Cognee sent no temperature at all on these providers … which is 1.0 for several Ollama
  models and produced varied, harder-to-parse extraction output"
  (https://docs.cognee.ai/guides/local-setup).
* **Single-machine scaling ceiling:** "Open-source cognee scales **vertically, in one process**: the API
  server runs one worker (`gunicorn -w 1`), and the improve and session locks, caches and semaphores are
  in-process only. More workers or replicas against the same data are **not coordinated**"
  (`.agents/skills/cognee-performance/SKILL.md:119-126`). Horizontal ingestion, enterprise GLiNER and the
  production Postgres graph adapter "are part of cognee's proprietary offering" (`:131-136`).

### The write cost, measured by them

Their own technical note (https://www.cognee.ai/token-cost-of-persistent-ai-memory, 2026-06-26, updated
2026-09-03, Vasilije Markovic): "During ingestion, documents are split deterministically into chunks.
**Each chunk is then processed by two independent LLM calls**" — one summary, one structured
entity/relationship extraction.

| | 10k run (`gpt-5-mini`) | 100k run (`claude-sonnet-4-5`) |
|---|---|---|
| corpus tokens | 16,075 | 201,987 |
| LLM calls | 429 (214 summarization + 214 graph) | 3,503 (1,751 + 1,751) |
| ingestion tokens | 395,515 | 4,659,246 |
| **ingestion multiplier `I/T`** | **24.6×** | **23.1×** |
| graph extraction share of ingestion | 72.0% | 74.0% |
| mean retrieved context per query | 1,118 tok | 1,864 tok |
| **break-even vs full-context** | **~26 queries** | **~23 queries** |

Two honest caveats they state: the corpus is "intentionally information-dense" — on ordinary prose (*War
and Peace*) graph extraction produces **~1.2 output tokens per input token** vs **8–10** on their dense
synthetic records, so "the ingestion costs reported below should be read in the context of this
deliberately dense corpus"; and embeddings are excluded because they run locally.

**Read onto us:** 23–24× corpus tokens of LLM work per ingest is the number that makes Cognee unusable
under our constraints. Our lore bibles are ~prose, so ~1.2× output/input plus prompt overhead is the
fairer estimate — still ≥2 forward passes of our 125B MoE per chunk, on the same 24 GB card that serves
the model. Cognee's own mitigation (GLiNER) trades the LLM for a 750 MB extractor + ~2 GB per in-flight
torch batch, and they label the open-source version "a **demo** … The production-grade version (higher
accuracy, broader label coverage) is available with an enterprise licence" (`.env.template:700-702`).
Our curation loop does the same job on a 30-minute timer with **zero** per-write inference in the
interactive path — that is a real architectural advantage, not a gap.

### Read cost

`recall()` with a completion type = 1+ LLM call; `CHUNKS`/`CHUNKS_LEXICAL`/`SUMMARIES` = zero
(§Read path). "The biggest win for chat-style use: `AUTO_FEEDBACK=false` … by default **every answered
turn** … makes one extra LLM call to analyze feedback"
(`.agents/skills/cognee-performance/SKILL.md:89-92`). Their `dry_run=True` cost estimator
(`cognee/modules/cognify/estimator.py`; `.agents/skills/cognee-performance/SKILL.md:14-19`) covers graph
extraction + summarization only, "not `improve()`, embeddings, or contradiction detection," and is
unavailable with GLiNER or a remote instance.

---

## Evaluation & published numbers (claimed vs reproduced)

### Claimed — BEAM, in-repo

`cognee/eval_framework/beam/REPORT.md` (marked "*Preliminary version*") + `README.md` table:

| BEAM context | score (0–1) | their own scope label |
|---|---|---|
| 100K | **0.79** | "Fixed hybrid retrieval; four evaluation rounds over 20 questions from **one held-out conversation**"; run std **0.005** |
| 10M | **0.67** | "**Exploratory** result; question-type routing selected **and scored on the same question set**, averaged over five rounds" |

Methodology facts from the report that decide how to read those numbers:

* **20 questions.** Not 500. The 100K score is "four QA-and-evaluation rounds over 20 questions" on a
  single held-out conversation (`REPORT.md §4.2`). Our harness is 500 questions × 3 repeats.
* **In-sample at 10M:** "Because selection and reporting use the same question set, this is an
  **in-sample** exploratory result" (`§3.1`).
* **BEAM-specific tuning is part of the number:** "We used standard Cognee memory components with
  **BEAM-specific data formatting, answer prompts, retrieval configuration, and evaluation tooling**"
  (`§1`), and the README repeats it: "The reported runs use Cognee's memory components with
  benchmark-specific data formatting, prompts, and retrieval configuration."
* **No ablations:** "The components appeared useful for different reasons, but **were not isolated** …
  we did not run controlled ablations. The scores therefore describe the combined pipeline, not the
  contribution of each component" (`§5`).
* **No hidden test set:** "BEAM exposes its conversations, questions, and rubrics and provides no hidden
  test set. Once those materials inform prompts, routing, or retriever settings, further score gains can
  reflect general improvement, adaptation to BEAM, or both, without proportionally strengthening the
  evidence that it generalizes" (`§5`).
* **Agentic retrieval was tested and dropped:** "we tested multi-turn agentic retrieval, but **excluded
  it from the results because it changes the interaction protocol from one-shot to iterative**" (`§3.1`).
* **Routing needed labels:** "Routed configurations use **BEAM's question-type labels** to select an
  answer prompt and retrieval settings. Outside a benchmark with explicit labels, the retrieval approach
  would need to be inferred from the question itself. One possible extension is an LLM-assisted router …
  **we did not evaluate one**" (`§5`). Note the tension with `query_router.py`: a *rule* router now
  exists in the product, but it routes to 3 destinations and was not what produced 0.67.
* **Preprocessing was needed:** "<0.2% of turns required cleanup … Those turns were passed through a
  **small LLM compression step**" (`§3.2`).
* **The 10M ingestion is not reproducible from the public repo:** "The public code does not include the
  BEAM-specific distributed orchestration from that run"; Appendix F "describes the remaining
  reproduction gap for the distributed 10M ingestion."

### Claimed — the paper

https://arxiv.org/abs/2505.24478, *Optimizing the Interface Between Knowledge Graphs and LLMs for Complex
Reasoning* (Markovic, Obradovic, Hajdu, Pavlovic; 30 May 2025). It is a **hyperparameter study**, not a
leaderboard: "Using three multi-hop QA benchmarks (HotPotQA, TwoWikiMultiHop, MuSiQue) we optimize
parameters related to chunking, graph construction, retrieval, and prompting," scored with EM/F1/DeepEval
LLM-correctness. The authors are Cognee Inc. employees (two also affiliated with Innorenew CoE /
University of Primorska). Note the paper optimizes **chunking and graph-construction parameters** — the
same knobs this file is about — which is the strongest available evidence that they are load-bearing.

### Reproduced — independently: nothing, and no LongMemEval number exists

* **No LongMemEval or LoCoMo score, period:** "Cognee **publishes no LongMemEval or LoCoMo score**. Its
  published numbers are on BEAM under its own methodology with an LLM judge, so they cannot be set beside
  the other systems' results"
  (https://mnemoverse.com/docs/library/ai-memory-solutions-2026-q3). Their own benchmarks page frames
  BEAM as "the closest match to the scale we care about" and restates 0.79/0.67 against "previously
  reported results of 0.73 and 0.64 respectively" (https://www.cognee.ai/ai-memory-benchmarks) —
  i.e. a comparison against the *benchmark's own* prior SOTA, not against a competitor run in the same
  harness.
* **The field's reproduction record is bad and their number is untested:** "Vendor benchmark numbers have
  not survived reproduction so far: Mem0's LongMemEval figure fell from a claimed 93.4% to **73.8%** under
  Maximem's harness (Maximem is itself a memory vendor)" (same URL; reproduction:
  https://www.maximem.ai/blog/state-of-ai-memory-2026-claimed-vs-observed). Cognee is not in Maximem's
  table either — nobody has run Cognee's write path through a LongMemEval harness at all.
* **Independent comparisons put Cognee on a different axis entirely:** "Choose **Cognee** when the main
  job is turning heterogeneous source knowledge into a queryable graph" and "Cognee rebuilds structure in
  the graph" (https://particula.tech/blog/agent-memory-frameworks-tested-mem0-zep-letta-cognee-2026).
  Nobody contests Cognee on accuracy; they contest it on operational weight.

**Verdict:** Cognee's numbers are honestly reported — the report is more self-critical than any in this
research set — but they answer a different question than ours (20 questions, one conversation, LLM judge,
benchmark-specific prompts, no ablations, no competitor-run reproduction). **Our 0.761 LongMemEval oracle
(500 × 3, temp 0.8, official checker, muted control) is a stronger protocol than their 0.79 BEAM.**
Publishing that contrast is legitimate; publishing 0.79 vs 0.761 as a comparison is not.

---

## What Cognee does better than Strata tier-3

Numbered, mechanism first, code path second.

**1. A tiling invariant on the document, enforced in code — the spine Strata threw away.**
*Mechanism:* stored chunk texts, concatenated in `chunk_index` order, must equal the document text
byte-for-byte. It is checked when reading the baseline (`chunk_offsets` raises
`IncrementalPlanError("stored chunk {i} does not tile the stored document text")`), re-checked after
planning (`validate_no_loss`: "Refuse any plan whose reassembly is not byte-identical to the new text …
With the standard TextChunker producing the region chunks, this **also verifies the external contract
that the chunker's output tiles its input**"), and used as a *crash detector* — after a crash before
publish, "the stored chunks then no longer tile the stored text, so the next touch **fails closed into a
full rebuild (self-heal)**."
*Code:* `cognee/modules/chunking/incremental_chunking.py:53-66, 289-321`;
`cognee/tasks/chunks/chunk_by_paragraph.py:9-31` ("exact text reconstruction");
`cognee/api/v1/update/incremental.py:41-45`.
*Why it beats us:* it is what makes character offsets **derivable for free** (§3), makes a citation
resolvable, makes a minimal diff possible, and turns silent data loss into a hard error. We have verbatim
sentences and `{file, n}` — the tiling is 90% there and unasserted.

**2. A document is a first-class, addressable object with a stable id equal to the ingest record.**
*Mechanism:* `Document.id = Data.id` (`classify_documents.py:166`), so `document_id` on a chunk is the
`dataId` a caller uses to map a citation back to the file they ingested; `Data` carries `name`,
`extension`, `mime_type`, `loader_engine`, `raw_data_location`, `content_hash`, `raw_content_hash`,
`external_metadata`, `system_metadata`, `node_set`, `token_count`, `data_size`, `importance_weight`,
`created_at/updated_at/last_accessed`, `pipeline_status` (`cognee/modules/data/models/Data.py:33-75`).
*Code:* as above; `cognee/modules/data/processing/document_types/Document.py:10-18`.
*Why it beats us:* we have `source=ingest-<date>` and `{file, n}` — a *string*, not a row. We cannot
list our documents, count rows per document, re-read a document's raw text, delete one, or expand a hit
into it, because there is no object to point at.

**3. Deterministic, LLM-free, answer-grounded citations.**
*Mechanism:* `format_chunk_references` — payload must supply `document_name` **and** a chunk number
(`chunk_number` or `chunk_index+1`) **and** text, else the bullet is dropped; candidates are filtered and
ranked by stopword-filtered term overlap **with the generated answer**, capped to 3–5 bullets, snippets
truncated to 160 chars; an ungroundable answer yields `""` "rather than presenting unverifiable retrieval
order as provenance." Structured form too: `EvidenceReference(kind, artifact_id, role, dataset_id,
source_ref_key, data_id, chunk_id, chunk_index, document_name, source_node_id, target_node_id,
relationship_name, rank, score)` — "intentionally contain identifiers and small locators only. Source text
remains in the vector or graph store rather than being duplicated in every search response."
*Code:* `cognee/modules/retrieval/utils/references.py:1-16, 71-74, 200-219, 277-360`;
`cognee/modules/search/models/EvidenceReference.py`; `hybrid/references.py`;
`cognee/modules/retrieval/utils/evidence.py:1-6` ("describe `used_as_context` lineage only; they do not
claim that every context artifact supports a particular statement").
*Why it beats us:* our injected block carries 96 notes with no per-note citation the model can emit, and
our `memory_search` tool returns rows, not locators. This is a ~120-line pure-Python module with **zero
inference** and it is exactly the "passage expansion for the model to cite" the brief asks about.

**4. Chunk-scoped ownership, so a document can be deleted without destroying shared knowledge or
leaving ghosts.**
*Mechanism:* an ownership map `node_owners` / `edge_owners` keyed by chunk ref-key; a chunk owns "EXACTLY
what its own extraction produced," shared output gets multiple owners and dies only with its last;
document-scoped output (the document node, NodeSet tags, ontology enrichment) survives chunks; edges
between two document-scoped nodes are excluded so they don't inherit a chunk's write group; and
relationship edges are attributed from the chunk's own `_produced_edge_identities` rather than by walking
`Entity.relations`, because walking "would hand a chunk everything downstream of any entity it mentions —
a chunk containing only `hole` owned the whole `hole -> rabbit -> alice -> queen` chain, and deleting the
chunk that contained `queen` left it alive as a ghost."
*Code:* `cognee/tasks/storage/chunk_ownership.py:1-200`.
*Why it beats us:* we cannot delete an ingested document at all. Deleting rows by `source=ingest-<date>`
is a date-scoped sledgehammer, and curation-loop facts derived from that document are indistinguishable
from conversation-derived ones.

**5. Chunk-level incremental update with a swappable policy, staged writes, and typed refusals.**
*Mechanism + code:* §Incremental updates in full — `incremental.py:1-45`,
`chunk_policy.py:1-67`, `incremental_chunking.py`, `RefusalReason` at `incremental.py:135-156`.
*Why it beats us:* re-ingesting an edited lore bible in Strata today means either duplicate rows (exact
dedup only catches unchanged sentences) or a manual shelf wipe. They re-chunk only the changed regions,
keep unchanged chunks' ids/entities/summaries from drifting, renumber survivors, and **prove** the result
re-assembles. The "policy plans completely, orchestrator trusts none of it, validate byte-for-byte" split
is the same shape as our merge-law philosophy.

**6. Three-lane hybrid with a *bounded* multiplicative blend and a single cut point.**
*Mechanism + code:* §Read path — `hybrid/ranking.py:26-67` (RRF with `k = max(30, min(60, 20+2·top_k))`,
factors `0.75 + 0.5·w`), `hybrid/candidates.py:34-77` (`finalize` is the only cut),
`hybrid/merge.py` (per-lane merge with `conversational_reserve`).
*Why it beats us:* (a) their side-signals are provably **bounded** — the shape we decided to test after
the −0.139 `knowledge-update` regression; (b) their rerank reorders the *same* candidate list before the
single cut, so a reranker cannot silently change the budget; (c) they merge two retrieval arms per-lane
with a reserve, which our A/B arms do not.

**7. A deterministic, measured-safety query router with a named rule and a bounded retry.**
*Mechanism + code:* `cognee/api/v1/recall/query_router.py` in full; retry at `recall.py:647-671`.
*Why it beats us:* we have zero query-side routing. Their safety rule — only route when the target is
"at least as good as the default … and does not add LLM calls without an unambiguous signal," route only
non-natural-language *shapes* or date-scoped questions, log the rule name, retry a *guess* once — is a
template we can apply to shelf routing without re-litigating the `knowledge-update` regression.

**8. Ontology grounding as canonicalization + closure + a logged drop ratio, with text untouched.**
*Mechanism + code:* §5A — `matching_strategies.py:20-51` (difflib, cutoff 0.8),
`RDFLibOntologyResolver.py:256-300` (fuzzy match + BFS subgraph),
`construct_data_points_and_edges_with_ontology.py:42-50, 132-252` (annotate vs strict, drop counters,
`EmptyOntologyInStrictModeError`), `DataPoint.py:71-77` (`ontology_valid`, `ontology_uri`).
*Why it beats us:* our keyword map classifies but never **canonicalizes** and never **reports coverage at
ingest time**. Their `annotate` default (never reject, always tag) plus a per-batch retained-% warning is
a strictly better failure posture than a silent `wild` bucket, at zero inference cost.

**9. A hierarchical summary index over the store (root + buckets), read as a context prelude.**
*Mechanism + code:* §5D — `global_context_index/{build,update,persist,summarize}.py`,
`GlobalContextSummary(level, is_root, summarized_in)`, `bucketing_strategy = "vector" | "graph"`,
`global_context.py:12-56`, `hybrid_retriever.py:263-279`.
*Why it beats us:* this is their direct answer to our "95% of rows on shelves too coarse to navigate" —
instead of more shelves, they build a *learned aggregation tree* offline and inject the root + nearest
buckets as a map above the passages. It is the tier-2 cartridge's missing sibling: a cheap "what is this
store about" prefix.

**10. A pre-ingestion file→group classifier with deterministic layers first and semantic clustering as
an opt-in.**
*Mechanism + code:* §5C — `cognee/tasks/presort/group_files.py:1-16` (code project → dot-namespaced
folder path → extension family → opt-in embedding+union-find `semantic` at cosine 0.6 / min size 2),
`apply_presort.py:1-16` (idempotent apply, duplicate and PII exclusion).
*Why it beats us:* our shelf assignment for a whole file is a human typing `--class`. Their layer 2
maps a directory tree onto a hierarchical shelf name for free, and layer 4 is one union-find over
vectors we already have.

**11. A cost estimator that refuses to guess: `dry_run=True` before ingesting.**
*Mechanism + code:* `cognee/modules/cognify/estimator.py`; `.agents/skills/cognee-performance/SKILL.md:14-19`;
the multiplier/break-even model in https://www.cognee.ai/token-cost-of-persistent-ai-memory.
*Why it beats us:* we can state our read-side numbers precisely and our write-side numbers only as "a
curation loop on a timer." A per-corpus token estimate before an ingest would make our 30-min timer
budgetable.

**12. Two views of every fact at read time — chunk text *and* a per-chunk summary, each embedded.**
*Mechanism + code:* `TextSummary.made_from: DocumentChunk` (`cognee/tasks/summarization/models.py:22-38`);
paired 1:1 by `summary_id_for_chunk`, missing chunks back-filled by id, summaries lazily fetched for the
ranked pairs only (`hybrid/chunks.py:85-104, 213-311`); fused by RRF.
*Why it beats us:* our rows are single-representation. A second, differently-phrased representation of
the same content measurably raises recall at fixed budget, and they get it from a *paired* row rather
than from a bigger `top_k`.

---

## What Strata does that Cognee does not

1. **A real inverted index.** FTS5 `bm25` persisted in the same SQLite file vs Cognee's per-process,
   per-dataset, in-memory full scan: `get_filtered_graph_data([{"type": ["DocumentChunk"]}])` → tokenize
   everything → score every chunk per query (`lexical_retriever.py:59-130`, `bm25_retriever.py:10-14`).
   At 575 ingest rows their approach is fine; at 10⁵ chunks it is a memory and first-query-latency
   problem. **Our lexical arm is strictly better-engineered than theirs.**
2. **Lexical and semantic are fused, not alternative.** We RRF bm25+vector on every query. They have to
   choose one type, and the router only reaches `CHUNKS_LEXICAL` when the whole query is a quoted string
   (`query_router.py:85-89`).
3. **Encryption at rest.** Our store is encrypted; Cognee's is plaintext files + LanceDB + SQLite, with
   multi-user *permissions* (`ENABLE_BACKEND_ACCESS_CONTROL`, per-dataset databases,
   `.env.template`) but no at-rest encryption story in the OSS docs we read.
4. **Zero-LLM writes.** Our curation loop batches on a timer; nothing in the interactive write path calls
   a model. Cognee's default write path is **2 LLM calls per chunk** and **23–24.6× corpus tokens**
   (§Local operation & cost). Their LLM-free path is a labelled demo with a 750 MB model and ~2 GB per
   batch, and enterprise-grade extraction is a paid licence.
5. **A deterministic merge law decides consolidation.** Cognee's dedup is *identity collapse* — name →
   uuid5 (`Entity.py:22`) — not semantic; its semantic conflict handling is an **opt-in LLM judge**
   (`detect_contradictions`, default off) that "swallows its own errors," and supersession requires
   **human-declared** single-valued relationships because "which ones hold a single target cannot be
   inferred" (`resolve_temporal_contradictions.py:14-19`). We have cosine-proposes + deterministic-law-decides,
   with measured false-merge rates behind it. On the *write* axis they are weaker and more expensive.
6. **Read-path near-dupe suppression.** Nothing in Cognee deduplicates the retrieved set: hybrid merge is
   per-lane by id (`merge.py`), `merge_scored` is "one hit per node" (`chunks.py:135-147`) — exact-id
   only. A chunk and its paired summary can both surface as separate content in the block.
7. **Tier-2 KV cartridge.** Restoring engine KV state in ~80 ms / 247 MB, seeded from shelves, capped at
   4,000 chars, has **no analogue** in Cognee. Their nearest thing is the GlobalContextSummary *text*
   prelude, which costs tokens, not a state restore.
8. **Server-side, harness-independent injection pinned per conversation.** Cognee's session layer is a
   per-user cache (SQLite/Redis, `SESSION_TTL_SECONDS=604800`) and "Session and trace search is **keyword
   overlap, not embeddings**" (`.agents/skills/cognee-recall/SKILL.md:88`). We inject a curated block into
   every turn for every client without the client asking.
9. **A measurement protocol with teeth.** Temp 0.8/1.0 never 0, ≥3 repeats for pass/fail, one fresh
   conversation per ask, memory muted via `X-Strata-Memory: off` on `/v1`, `usage.prompt_tokens` asserted
   against the local render. Cognee's discipline is real but different: byte-identical defaults, opt-in
   features, unit tests — and their headline 10M result is **in-sample** with 20 questions.
10. **Everything on one consumer GPU, one file, no external service.** Cognee's default is three
    databases (relational + LanceDB + a graph store) and, for quality, an external LLM; scaling past one
    process is a paid feature (`.agents/skills/cognee-performance/SKILL.md:120-135`).

**Shared values worth crediting honestly:** opt-in + byte-identical default ("Off by default, so returned
objects and the prompt stay exactly as before", `hybrid_retriever.py:104-106`), deterministic-where-possible
with LLM-judgment quarantined to opt-in stages, typed errors over free text, and unusually honest
evaluation prose. The *philosophy* is ours; the *cost profile* is not.

---

## Portable ideas for Strata tier-3

Ranked by (expected gain × confidence) ÷ cost. "Our harness" = 500 LongMemEval questions × 3 repeats at
temp 0.8, official checker, muted control arm; mechanism-level = `tools/lme_retrieval_ab.py --offline`
(~45 s); probes = `m4_shelf_nav.py`, `m4_position.py`.

**1. Give ingested rows a document spine: `doc_id` + dense `seq`, plus a tiling assertion.**
*Mechanism:* Cognee's spine is exactly `Document(id = Data.id)` + `chunk_index` + the byte-exact tiling
invariant, and *only* those three (`Data.py`, `classify_documents.py:166`,
`incremental_chunking.py:53-66, 309-321`). No sections, no offsets, no pages.
*Mapping:* `m2_ingest.py` writes a `documents` row per file (`id`, `path`, `sha256`, `bytes`,
`tokens`, `ingest_date`, `class`, `title`) and adds `doc_id INTEGER REFERENCES documents(id)` +
`seq INTEGER` (dense, 0-based, gap-free per doc) to ingest rows. Then assert on write and on
`integrity_check`: `SELECT (SELECT group_concat(text,'' ORDER BY seq) FROM rows WHERE doc_id=?) =
<stored source text>` — store the source text (or its hash plus a chunked side table) so the check is
executable. Offsets become derivable by prefix-summing `seq`, exactly as `chunk_offsets` does.
*Cost:* one migration, ~80 LOC, no inference, no new dependency; +2 columns; the assertion is O(rows in
one doc) and belongs on the timer, not the hot path.
*Measure:* (a) **expansion precision** — for each `m4_position.py` hit, expand to ±2 rows and re-score
right-fact-in-block; expect +1–3 points on ingest-heavy questions and 0 on conversation-only ones;
(b) **citation-ability** — a 50-question hand-scored probe: can the model emit `file #seq` and have it
resolve to the source? today: structurally no; (c) harness unchanged (proof we didn't buy it with
tokens).
*Risk:* the tiling assertion fails if `m2_ingest` ever drops whitespace-only or empty sentences — verify
`n` is dense on all 575 existing rows before promising byte-exactness, and fall back to
"gap-free `seq`, offsets approximate" if not. Also: re-ingesting an edited file renumbers `seq`; idea 2
fixes that. **Highest value/cost item in this list.**

**2. Content-derived row identity for ingest rows (`uuid5(doc_id : sha256(text) : occurrence)`).**
*Mechanism:* `cognee/modules/chunking/chunk_id.py:1-22` — they migrated *away from* positional
`uuid5(doc_id-chunk_index)` because "every id downstream of an edit shift or collide[d] once chunk indices
moved … an unchanged chunk keeps its id no matter where it sits, and re-ingesting identical content is
idempotent," with an occurrence counter so "two equal paragraphs stay two nodes."
*Mapping:* for ingest rows only (conversation rows have no content-stable parent), derive the row id and
keep `seq` as a mutable position, exactly as Cognee renumbers `kept_moves` without touching ids
(`chunk_policy.py:62-63`).
*Cost:* ~30 LOC in `m2_ingest.py` + one index; no inference.
*Measure:* re-ingest the same file twice → **zero** new rows (today: exact-text dedup already gives this
within a file; this makes it work across renames and re-shelving). Then edit one sentence, re-ingest, and
count rows changed — target is "only the sentences after the edit," which needs idea 7 to fully land.
Confirm the near-duplicate family at rows 2550-2556 is untouched.
*Risk:* an id change on an existing row breaks `superseded_by` pointers. Migrate by *adding* the derived
id as a lookup key (`content_key`) rather than replacing `rowid`, and only collapse duplicates later.

**3. Deterministic answer-grounded Evidence block for `memory_search` and for injected notes.**
*Mechanism:* `references.py:277-360` — stopword-filtered term overlap between the **generated answer** and
each candidate; drop anything with zero overlap; rank by overlap; 3–5 bullets; 160-char snippets;
require locator fields or skip the bullet; return `""` rather than dress retrieval order up as
provenance.
*Mapping:* pure Python over the rows we already returned. Emit `Evidence:` lines of the form
`"<snippet 160 chars> — <file> #<seq>"` (needs idea 1). Attach to `memory_search` results first (cheap,
no prompt change), then optionally to the injected block header.
*Cost:* zero inference, ~120 LOC, a stopword list (theirs is ~120 words,
`references.py:47-190`).
*Measure:* citation **precision** on 50 hand-scored answers (fraction of emitted locators that contain
the claimed fact) and the **hallucinated-locator rate** (locators emitted for rows that were never in the
block). Both are new instruments; target ≥95% precision. Harness accuracy should be flat.
*Risk:* term-overlap ranking is biased toward long rows — our rows are ~160 chars, so this bias is
near-absent (their 8k-token chunks make it real and they still accepted it).

**4. A rule-based, no-LLM query router — over *strategies* first, shelves second.**
*Mechanism:* `query_router.py` — ordered regex, first match wins, named rule in the log, a stated safety
rule ("only route to something at least as good as the default, and never add an LLM call without an
unambiguous signal"), and a single bounded retry of a *routed* guess only.
*Mapping:* three arms we already have: (i) whole query is one quoted string → **FTS5-only** arm (exact
phrase — our rows are verbatim sentences, so this is *more* likely to pay off than for their prose);
(ii) date-shaped query → recency-boosted arm; (iii) otherwise current hybrid. Later, a fourth:
shelf-shaped query ("what does the lore say about X") → restrict `class` **only if** the classifier's
margin passes their own 2-token bar, else don't filter (their `annotate`-not-`strict` default is exactly
this caution).
*Cost:* zero inference, ~120 LOC of regex + a routing log column.
*Measure:* `lme_retrieval_ab.py --offline` per arm; then the harness **sliced by question type**, with
`knowledge-update` as the guard rail (this is where our current rule demotion cost −0.139, so a router
that only fires on *shape*, never on topic, is the safer design). Log rule name + arm for every query and
report the distribution.
*Risk:* routing to a narrower arm turns a retrieval miss into a silent zero-hit. Copy their fix: retry
once as the default arm, and only when the *router* chose. Also copy their `CYPHER` ban — never let a
routed arm reach a mutating operation.

**5. Bounded multiplicative side-signals, and one cut point.**
*Mechanism:* `ranking.py:35-52` — every side-signal is a factor in `[0.75, 1.25]` applied to the RRF
score, so it can nudge but never dominate; the tie-break is fully deterministic
`(final, rrf, min(raw_rank), id)`. `candidates.py:63-77` — `finalize()` is the only place a list is cut.
*Mapping:* re-express our blend (rank/recency/confidence) as RRF × bounded factors instead of a weighted
sum, and move our reranker to a `prioritize()`-style reordering of the uncut candidate list, with the
budget enforced in one place.
*Cost:* ~40 LOC, zero inference.
*Measure:* the `knowledge-update` slice, 3 repeats — the explicit hypothesis is that a bounded demotion
recovers most of the −0.139 while keeping the `multi-session` gain. This is the idea we have already
decided to test; Cognee supplies the exact functional form and a defensible bound.
*Risk:* none material; keep the old blend as a byte-identical arm.

**6. Adaptive RRF `k`, and a per-lane merge with a reserve for the second arm.**
*Mechanism:* `_rrf_k = max(30, min(60, 20 + 2·top_k))` (`ranking.py:60-61`);
`merge_hybrid_results` merges **each lane separately under its own budget** with
`conversational_reserve` protecting slots for the secondary arm, and rebuilds derived maps so nothing
references a dropped row (`merge.py:1-61`).
*Mapping:* one constant in our fusion; and adopt the per-lane merge for our A/B arms so a second retrieval
pass can't spend the whole block.
*Cost:* one line for `k`; ~50 LOC for the merge.
*Measure:* `lme_retrieval_ab.py --offline` over `k ∈ {30, 45, 60}` at our actual cut (48 recall_limit →
their formula gives 60 anyway at ≥20, so expect ~0 at our budget and a real effect at small cuts; measure
at `top_k` 5/10/20 to know whether the constant is load-bearing before shipping it).
*Risk:* none. Note their `k` only differs from ours below `top_k=20`, so this is a small-store win, not a
big-store one.

**7. A diff-based re-ingest for edited documents, with typed refusals.**
*Mechanism:* `incremental_chunking.py` (paragraph-anchored diff → expand hunks to chunk boundaries →
merge touching regions → re-chunk only regions → `validate_no_loss`) + `RefusalReason` so
"a permanent misconfiguration … looked exactly like a first ingestion" (`incremental.py:135-156`).
*Mapping:* sentence-granular version of the same algorithm over `seq`-ordered rows: diff old vs new
source text, map changed spans to the sentences they overlap, re-embed and re-shelve only those, renumber
the rest. Our rows are already sentence-granular, which makes this **simpler** than their chunk version.
*Cost:* ~200 LOC, CPU-only (difflib + our embedder), no LLM. Cheapest at our granularity.
*Measure:* re-ingest a lore bible with one sentence edited mid-file: rows re-embedded should be ~1, not
575; wall-clock and embed count before/after; and a `refusal_reason` column logged for every fallback.
*Risk:* their guard rails are mandatory, not optional: `chunker_id`-style stamp (which splitter built the
row), a tiling check, and `validate_no_loss` equivalent. Without them a bad diff silently corrupts the
shelf, and we'd never know.

**8. Shelf coverage telemetry + canonicalization, borrowed from ontology grounding.**
*Mechanism:* `annotate`-by-default (never reject, always tag), `find_closest_match` = difflib cutoff 0.8
with exact-match-first (`matching_strategies.py:35-51`), NodeSet names normalized so "two spellings that
normalize alike … are one node set" (`node_set.py:18-31`), and a per-batch
`"Strict ontology mode dropped N of M extracted node(s) (P% retained)"` warning
(`construct_data_points_and_edges_with_ontology.py:245-252`).
*Mapping:* (a) normalize shelf names on write (case/space/apostrophe) so near-miss shelves collapse;
(b) add a canonical-alias table so `class` values are vocabulary, not free text; (c) log a per-batch
`shelved N of M (P% wild)` line from the curation loop and from `m2_ingest`, and **fail loudly** when the
classifier resolves to nothing (their `EmptyOntologyInStrictModeError`).
*Cost:* zero inference, one small table + two log lines.
*Measure:* shelf-cardinality before/after (how many distinct `class` values collapse); `wild` % trend per
batch instead of one offline number; and `m4_shelf_nav.py` right-shelf rate.
*Risk:* collapsing two shelves that meant different things ("MILITARY" vs "military-history"). Keep the
original string in a `class_raw` column for one release.

**9. A hierarchical shelf index (root + buckets) as the cartridge's seed, or as the block prelude.**
*Mechanism:* GlobalContextSummary tree built bottom-up over summaries with `max_bucket_size` /
`placement_distance_threshold` / `min_overlap`, `vector` or `graph` placement, read top-down as a
`## Global context` prelude of root + nearest buckets
(`global_context_index/*`, `global_context.py:12-56`, `hybrid_retriever.py:263-279`).
*Mapping:* for the 575-row shelf and the 18% `wild` bucket: one **root summary** per shelf plus
~⌈n/8⌉ buckets, produced on the 30-min timer (never on the write path), embedded with our existing
CPU embedder, injected as a 2–3 line prelude above the notes — or, better, used as the **tier-2
cartridge seed** in place of two raw shelves.
*Cost:* LLM per bucket (offline, timer only) + one embed per bucket; ~150 LOC; storage ~1 row per
bucket. This is the only idea here that spends inference, and it spends it off the hot path.
*Measure:* `m4_shelf_nav.py` right-shelf rate with and without the prelude; harness on the question
slices that need document-wide context (multi-hop, summarization); and for the cartridge variant, a
restore-time and 4,000-char-fit check against the current two-shelf seed.
*Risk:* summaries lie, and a wrong root is a *global* wrong prior — worse than a wrong shelf. Gate on
`class`-level buckets we can hand-check first, and keep the prelude opt-in with a byte-identical default.

**10. A `dry_run` ingest cost estimate.**
*Mechanism:* `cognee/modules/cognify/estimator.py` + `remember(dry_run=True)` returning per-stage token
counts and approximate cost, no LLM calls.
*Mapping:* for `m2_ingest.py` and for the curation timer: tokenize the input, multiply by a measured
per-row factor, print `~N rows, ~M tokens, ~T minutes` before touching the store.
*Cost:* ~40 LOC, zero inference (the multiplier comes from one instrumented run).
*Measure:* predicted vs actual rows/tokens/wall-clock over 5 files; report the error. Then use it to
budget the timer.
*Risk:* the multiplier drifts with input style; publish the fit window like Cognee did (they explicitly
flagged their corpus as 8–10× denser than prose).

### Explicitly not portable

* **The graph store.** A 24 GB GPU shared with a quantized 125B MoE, one SQLite file, no cloud: adding
  Kuzu/Ladybug/Neo4j is a second database for a benefit (entity-relationship traversal) that our measured
  residual error says is *not* our bottleneck — 244 of 346 wrong lines already had every gold session
  present. Cognee's own contradiction detector excludes the structural edges from fact comparison, so even
  they don't use the spine as a retrieval signal.
* **Per-chunk LLM extraction and summarization.** 23–24.6× corpus tokens, 2 calls per chunk
  (https://www.cognee.ai/token-cost-of-persistent-ai-memory). Fatal under constraint 1.
* **GLiNER as the extractor.** 750 MB model + ~2 GB per in-flight torch batch on CPU, and the OSS version
  is self-described as "a demo" with the good one behind a licence (`.env.template:700-712`).
* **Their BM25.** Do not adopt an in-memory full scan to gain a feature we already have faster.
* **Ontology files (OWL/RDF, rdflib).** Our taxonomy is 17 shelf marks and a keyword map; an RDF stack is
  1,433 LOC to do what difflib + an alias table does for us. Take the *ideas* (canonicalize, annotate
  don't reject, log the drop ratio), not the dependency.
* **20 search types.** Ours is one recall path + one tool + a UI. Their type list is a matrix of
  retriever × prompt × LLM budget; seven of the types silently ignore the node-set filter, which is the
  maintenance cost of that breadth.

### The central judgment, restated

**Keeping a document spine buys Strata citation and deletion, and buys them almost for free; it does not
buy retrieval precision, and Cognee's own numbers do not support the claim that it would.**

Three findings drive this. First, Cognee has **no section hierarchy, no page numbers, and no stored
offsets** — the spine is `doc_id` + ordinal + tiling, and nothing else
(`PdfDocument.py:18-22` throws the page number away; `UnstructuredDocument.py:24-28` flattens structure
to `str(el)` joined with blank lines). Anyone claiming Cognee "keeps document structure" is describing the
two-level skeleton, not an outline. Second, the spine is used for **exactly two things in their code** —
citation locators (`references.py:277-360`, `EvidenceReference`) and safe deletion
(`chunk_ownership.py`) — and is explicitly excluded from fact comparison (`detect_contradictions.py:38-40`)
and never used to widen a hit (no adjacency logic anywhere in `cognee/modules/retrieval/`). Third, their
precision comes from **chunk size**: default ~8,191 tokens, so a hit is already a passage and no expansion
is needed — while their measured retrieved context is only **1,118–1,864 tokens for the whole answer**
(https://www.cognee.ai/token-cost-of-persistent-ai-memory), the same order as our 24k-char block. **We
already spend the same context; we just spend it on 96 disconnected 160-char notes instead of on 3–5
contiguous passages.** That reframing is the actual prize: with a spine, the same 24k chars can carry
3–5 *readable* passages with locators, which is the shape a model can cite and reason over — and our
measured failure mode is reasoning with full evidence present, not missing evidence.

For the **cartridge**, a document is a strictly better unit than a shelf: a shelf of 575 unrelated rows is
not a coherent 4,000-char prefix, whereas a document's first N sentences are, and Cognee's root-summary
pattern (§5D) says the coherent prefix should be a *summary of* the unit, not the unit's rows.

Skeptical caveats we owe before building: 575 rows is small — the spine's deletion story is nearly moot at
that size and the citation story is the one that pays; LongMemEval has **no** document-expansion questions,
so this will not show up in the harness and needs its own instrument (idea 3's locator-precision probe and
idea 1's ±2 expansion score); and if the `n` values in existing ingest rows are not gap-free, the tiling
invariant must be built going forward and back-filled approximately, which weakens the offset-derivation
argument until the corpus is re-ingested once.

---

## Search log

Web searches (Brave Search API, 2026-10-14/15, `extra_snippets` on), in order:

1. `Cognee ECL pipeline Extract Cognify Load`
2. `Cognee DataPoint graph model schema knowledge graph`
3. `Cognee ontology support OWL knowledge graph grounding`
4. `Cognee node sets datasets document chunks`
5. `Cognee chunking strategy document chunk provenance`
6. `Cognee hybrid retrieval vector graph BM25 lexical`
7. `Cognee BEAM benchmark 100K 10M conversational memory results`
8. `Cognee benchmark reproduction independent evaluation LongMemEval`
9. `Cognee local SQLite LanceDB Kuzu default backends no cloud`
10. `Cognee vs Zep vs Mem0 comparison agent memory benchmark`
11. `Cognee paper arxiv 2505.24478 Optimizing Interface Knowledge Graphs LLM results`
12. `Cognee GLiNER local small models no API key CPU`
13. `Cognee global context index hierarchical summary buckets`
14. `Cognee delete document chunk-level ownership graph`
15. `Cognee reranker cross-encoder retrieval boost`
16. `Cognee temporal graph events timestamps bi-temporal valid_to`
17. `Cognee session distillation improve memory consolidation`
18. `Cognee loaders supported file types document ingestion`
19. `Cognee ingestion cost tokens per document LLM calls expensive`
20. `Cognee LanceDB performance scaling thousands documents slow`
21. `Cognee ontology strict mode drops entities issue criticism`
22. `Cognee ladybug graph store replaces kuzu embedding model dimensions`

Pages/documents fetched and read:

* https://github.com/topoteretes/cognee (README: quickstart, no-LLM path, BEAM table with scope caveats,
  Postgres-graph "demo feature" warning, v1.6.0/v1.6.1 release notes, `external_metadata` on every chunk)
* https://docs.cognee.ai/llms.txt (docs index)
* https://docs.cognee.ai/core-concepts/architecture (three stores, what is stored where, which store
  matters in which phase)
* https://docs.cognee.ai/guides/ontology-support (annotate vs strict, OWL constructs read, drop-ratio
  warning, `EmptyOntologyInStrictModeError`, no hot reload)
* https://www.cognee.ai/token-cost-of-persistent-ai-memory (the two-calls-per-chunk table, 24.6×/23.1×
  multipliers, 1,118/1,864-token retrieval, 23–26-query break-even, the 1.2-vs-8–10 density caveat)
* https://mnemoverse.com/docs/library/ai-memory-solutions-2026-q3 (independent six-way comparison,
  2026-09-28; "Cognee publishes no LongMemEval or LoCoMo score"; Ladybug succeeds Kuzu; two published
  corrections about Cognee; the Maximem reproduction of Mem0 93.4%→73.8%)
* third-party context, read as claims: https://rywalker.com/research/cognee,
  https://particula.tech/blog/agent-memory-frameworks-tested-mem0-zep-letta-cognee-2026,
  https://www.cognee.ai/grounding-ai-memory, https://www.cognee.ai/ai-memory-benchmarks,
  https://docs.cognee.ai/guides/local-setup, https://docs.cognee.ai/setup-configuration/overview,
  https://docs.cognee.ai/core-concepts/further-concepts/node-sets,
  https://docs.cognee.ai/core-concepts/further-concepts/global-context-index,
  https://docs.cognee.ai/core-concepts/further-concepts/ontologies,
  https://docs.cognee.ai/core-concepts/main-operations/cognify,
  https://docs.cognee.ai/core-concepts/main-operations/legacy-operations/{search,delete},
  https://docs.cognee.ai/guides/custom-graph-model, https://arxiv.org/abs/2505.24478,
  https://thedataquarry.com/blog/from-kuzu-to-ladybug/, https://www.lancedb.com/blog/case-study-cognee

Code read locally (`/tmp/opencode/research/cognee` @ `f154b32a`, v1.6.4):

* **chunking:** `modules/chunking/models/DocumentChunk.py`, `Chunker.py`, `TextChunker.py`,
  `chunk_id.py`, `chunk_policy.py`, `incremental_chunking.py` (full), `external_metadata.py`,
  `text_chunker_with_overlap.py`; `tasks/chunks/chunk_by_paragraph.py`
* **documents:** `modules/data/models/{Data,Dataset}.py`,
  `modules/data/processing/document_types/{Document,TextDocument,PdfDocument,UnstructuredDocument}.py`,
  `tasks/documents/{classify_documents,extract_chunks_from_documents}.py`
* **engine:** `infrastructure/engine/models/DataPoint.py` (full), `modules/engine/models/{Entity,node_set}.py`,
  `tasks/summarization/models.py`
* **cognify:** `api/v1/cognify/cognify.py:572-751`, `tasks/graph/{extract_graph_from_data,
  detect_contradictions,resolve_temporal_contradictions}.py`, `tasks/storage/{chunk_ownership,close_node,
  index_graph_edges}.py`, `tasks/provenance/record_provenance.py`, `tasks/graph/gliner_demo/tasks.py`
* **retrieval:** `modules/retrieval/hybrid_retriever.py` (full), `hybrid/{chunks,ranking,merge,candidates,
  facts,context,references}.py`, `{lexical_retriever,bm25_retriever,chunks_retriever,
  graph_completion_context_extension_retriever}.py`, `utils/{references,global_context,evidence}.py`,
  `modules/search/types/SearchType.py`, `modules/search/operations/select_search_type.py`,
  `api/v1/recall/query_router.py` (full)
* **ontology:** `modules/ontology/{matching_strategies,base_ontology_resolver,
  construct_data_points_and_edges_with_ontology}.py`, `rdf_xml/RDFLibOntologyResolver.py`
* **index/hierarchy:** `tasks/memify/global_context_index/{models,update,bucketing_strategy,constants}.py`
* **updates:** `api/v1/update/{incremental,update}.py`
* **config/backends:** `.env.template`, `infrastructure/databases/vector/embeddings/config.py`,
  `infrastructure/databases/vector/lancedb/LanceDBAdapter.py:78-101`
* **in-repo docs (agent skills, unusually good):** `.agents/skills/cognee-{performance,ingestion,recall,
  forget}/SKILL.md`, `CLAUDE.md:875`, `cognee/eval_framework/beam/REPORT.md`

---

## Unverified claims

* **BEAM 0.79 / 0.67 have never been reproduced by anyone.** The report itself flags no ablations, an
  in-sample 10M configuration, 20 questions on one conversation, benchmark-specific prompts, and a
  distributed 10M ingestion whose orchestration "the public code does not include." No third party has
  run Cognee on LongMemEval or LoCoMo at all
  (https://mnemoverse.com/docs/library/ai-memory-solutions-2026-q3). **Do not quote 0.79 beside our
  0.761.**
* **The 23–24.6× ingestion multiplier is measured on a deliberately dense synthetic corpus** where graph
  extraction emits 8–10 output tokens per input token vs ~1.2 on prose
  (https://www.cognee.ai/token-cost-of-persistent-ai-memory §4). The multiplier for lore bibles, specs and
  policy papers is **not published** — my "≥2 forward passes per chunk, prose-like" estimate in §7 is my
  reading of their call-count statement plus their own density caveat, not a measurement.
* **"GLiNER extraction is LLM-free and good enough" is unaudited.** The repo calls the OSS extractor "a
  demo of Cognee's small-model pipeline" and sells a more accurate, broader-label version as enterprise
  (`README.md`; `.env.template:700-702`). No accuracy figure for GLiNER-vs-LLM extraction is published in
  the OSS repo, and I found no independent measurement.
* **The Postgres-as-graph-store path is a licensed product, not OSS:** "Using Postgres as a graph store is
  currently released as a demo feature. The production-ready version is available as a licensed product"
  (`README.md`). Whether the OSS Postgres graph path is usable for real work is untested by me and
  unquantified by them.
* **Ontology grounding's retrieval benefit is asserted, not measured.** "Improves graph completion answers
  for domain-specific queries" (https://docs.cognee.ai/guides/ontology-support) carries no A/B number in
  the docs or the paper I read; the paper optimizes chunking/graph-construction/retrieval/prompting
  hyperparameters on HotPotQA/TwoWikiMultiHop/MuSiQue and I did not find an ontology on/off arm
  (https://arxiv.org/abs/2505.24478).
* **I did not run Cognee.** Everything in this file is code- and doc-derived. Specifically unmeasured by
  me: the in-memory BM25 scan's latency at 575 / 5k / 50k chunks; whether the default 8,191-token chunk
  actually over-runs fastembed's 512-token window in practice (the repo asserts it does,
  `cognee-performance/SKILL.md:150-154`); LanceDB payload-filter cost for `node_name`; and whether
  `HybridRetriever`'s three-lane fan-out fits in our 24 GB alongside the engine.
* **`chunk_index` is renumbered on incremental update** (`chunk_policy.py:62-63`, `kept_moves` maps stored
  id → final index) and the citation number is derived from it (`references.py:213-217`), so **a citation
  to "document 3, chunk 12" is not stable across an edit of that document.** Their docs don't say this;
  it is my inference from the code and I did not test it. If we adopt `#seq` locators, we inherit the
  same instability, and idea 2 (content ids) is the mitigation.
* **Whether Cognee's session layer can replace our injected block is untested.** "Session and trace search
  is keyword overlap, not embeddings" (`.agents/skills/cognee-recall/SKILL.md:88`) and the session cache
  is per-user SQLite/Redis with a 7-day TTL — but nobody has measured its recall against a curated block,
  and I did not read `modules/session_lifecycle/` in depth.
* **Ladybug vs Kuzu:** the repo contains both `infrastructure/databases/graph/{kuzu,ladybug}` and a
  compatibility shim (`graph/config.py:79-80`), and an external page says Ladybug is a Kuzu fork by a
  different team (https://thedataquarry.com/blog/from-kuzu-to-ladybug/). I did not verify which is the
  shipped default in v1.6.4 or whether either is still maintained.
