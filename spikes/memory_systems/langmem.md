# LangMem (langchain-ai/langmem) — deep read for Strata tier-3

Researched 2026-10-14 against `main` @ `48e3c11f5bb527282c7d5339c6a87a0b35abccfc`
("Build(deps): Bump the uv group across 1 directory with 4 updates (#191)", committed
2026-10-02), shallow-cloned to `/tmp/opencode/research/langmem` (4.0 MB, 6,940 LOC in
`src/langmem/`, 2,700 LOC of markdown docs, MIT, `langmem==0.0.30`).
Repo: https://github.com/langchain-ai/langmem · docs:
https://langchain-ai.github.io/langmem/ · launch post:
https://www.langchain.com/blog/langmem-sdk-launch (2025-02-18).
GitHub metadata at time of writing: 1,704 stars, 195 forks, 75 open issues, created
2025-01-21, last push 2026-10-02 (GitHub API `search_repositories`).

Line numbers below are from that clone and are exact. trustcall (the extraction engine
LangMem delegates to) was read from the pinned wheel `trustcall-0.0.39`
(`pyproject.toml:12` requires `trustcall>=0.0.39`) unpacked to
`/tmp/opencode/research/tc/tc/trustcall/`; its line numbers are from that unpack.

Two notes on the research brief's assumed layout: **there is no `langmem/schemas.py` and
no `langmem/memory/` package at 0.0.30.** The equivalents are
`src/langmem/knowledge/extraction.py` (memory managers + store upserts),
`src/langmem/knowledge/tools.py` (hot-path agent tools),
`src/langmem/prompts/` (procedural memory / prompt optimizers),
`src/langmem/short_term/summarization.py` (working memory), and
`src/langmem/reflection.py` (the background scheduler). There is also **no
`evals/` or `benchmarks/` directory** (see [Evaluation tooling](#evaluation-tooling-what-they-actually-measure)).

---

## What it is

LangMem is a **library of memory patterns layered on a document store**, not a memory
engine. Its own conceptual guide states the whole contract in four lines
(`docs/docs/concepts/conceptual_guide.md:7-11`):

> "At its core, each memory operation in LangMem follows the same pattern:
> 1. Accept conversation(s) and current memory state
> 2. Prompt an LLM to determine how to expand or consolidate the memory state
> 3. Respond with the updated memory state"

Everything meaningful is done by an LLM call; LangMem supplies prompt templates, tool
schemas, retry/validation, storage plumbing, and a background scheduler. It has three
product surfaces:

| Surface | Code | What it does |
| --- | --- | --- |
| Knowledge (semantic/episodic) | `src/langmem/knowledge/extraction.py` (2,113 LOC) | extract + consolidate + upsert into a LangGraph `BaseStore` |
| Hot-path tools | `src/langmem/knowledge/tools.py` (530 LOC) | `manage_memory` / `search_memory` tools an agent calls itself |
| Procedural (behavior) | `src/langmem/prompts/` (1,515 LOC) | prompt optimizers: `gradient`, `metaprompt`, `prompt_memory` (`prompts/optimization.py:23`) |
| Working memory | `src/langmem/short_term/summarization.py` (860 LOC) | token-threshold message summarization |
| Scheduling | `src/langmem/reflection.py` (474 LOC) | debounced background reflection, local thread or remote LangGraph run |

Storage is delegated to LangGraph's `BaseStore`: JSON documents under a namespace tuple +
key, with optional single-vector semantic search (pgvector in `PostgresStore`) and
content filtering
(https://www.langchain.com/blog/semantic-search-for-langgraph-memory). There is no
lexical/BM25 arm and no reranker anywhere in the repo.

Two design statements worth keeping in mind for the comparison:

- `docs/docs/concepts/conceptual_guide.md:38`: "If the system over-extracts, this could
  lead to reduced precision of memories when your agent needs to search the store. If it
  under-extracts, this could lead to low recall. LangMem uses a memory enrichment process
  that strives to balance memory creation and consolidation, while letting you, the
  developer, customize the instructions to further shift the strength of each."
- LangChain's platform-level memory doc names the failure mode even more plainly and then
  outsources the fix: "the model must now *delete* or *update* existing items in the list,
  which can be tricky. In addition, **some models may default to over-inserting and others
  may default to over-updating.** See the [Trustcall](https://github.com/hinthornw/trustcall)
  package for one way to manage this and **consider evaluation (e.g., with a tool like
  LangSmith) to help you tune the behavior**"
  (https://docs.langchain.com/oss/python/concepts/memory, "Collection").

That sentence is the entire delta between LangMem and Strata tier-3: LangMem diagnoses
over-insert/over-update, mitigates it with schema-validity machinery, and hands the
*quality* problem to the developer's eval harness. Strata closed it with deterministic
laws and measured the residual (44% and 39% false-merge rates before the laws — Strata
brief; audit tool `tools/m2_merge.py`).

---

## Memory types & schemas

### The taxonomy

The table appears verbatim in the launch blog, the conceptual guide
(`conceptual_guide.md:24-28`) and the platform docs
(https://docs.langchain.com/oss/python/concepts/memory):

| Memory Type | Purpose | Agent Example | Typical Storage Pattern |
| --- | --- | --- | --- |
| Semantic | Facts & Knowledge | user preferences; knowledge triples | **Profile or Collection** |
| Episodic | Past Experiences | few-shot examples; summaries of past conversations | **Collection** |
| Procedural | System Behavior | core personality and response patterns | **Prompt rules or Collection** |

The three are not three subsystems. They are **three uses of one mechanism**
(`create_memory_manager(model, schemas=[...], instructions=...)`), plus a separate prompt
optimizer for the procedural case. What differs is (a) the Pydantic schema you pass,
(b) whether inserts are allowed, and (c) which namespace you point at.

### What a "type" actually is in code

A type is a Pydantic class that becomes a **tool** the model may call. Default:

```python
# src/langmem/knowledge/extraction.py:86-92
class Memory(BaseModel):
    """Call this tool once for each new memory you want to record. Use multi-tool
    calling to record multiple new memories."""

    content: str = Field(
        description="The memory as a well-written, standalone episode/fact/note/preference/etc."
        " Refer to the user's instructions for more information the preferred memory organization."
    )
```

Multiple types in one pass are supported (`schemas=[Triple, Preference, Relationship]`,
`docs/docs/guides/extract_semantic_memories.md:168-174`); each class name becomes a
distinct tool and the manager records which one fired:

```python
# src/langmem/knowledge/extraction.py:864-865
self.schemas = schemas if schemas is not None else (Memory,)
self.schema_name_map = {schema.__name__: schema for schema in self.schemas}
```

and the persisted document is exactly `{"kind": <ClassName>, "content": <dump>}`
(`extraction.py:1114`, `:1122`; visible in `examples/standalone_examples/README.md`
output "Kind: PreferenceMemory"). **`kind` is the only type marker LangMem persists**, and
nothing in the read path filters or boosts on it.

### Profile vs collection

This is the single most useful distinction in the repo, and it is a *one-flag* difference:

- **Collection** — `enable_inserts=True` (default): unbounded knowledge, searched at
  runtime. Docs: `conceptual_guide.md:34-40`.
- **Profile** — `enable_inserts=False`: "it will only ever manage a single instance of the
  memory" (`docs/docs/guides/manage_user_profile.md:63`), updates in place; seeded on
  first use via `default=` / `default_factory=`, which writes to a fixed store key
  `"default"` (`extraction.py:1040-1063`, `:1696-1703`).

Selection guidance, verbatim (`conceptual_guide.md:177`):

> "Choose between profiles and collections based on how you'll use the data: profiles
> excel when you need quick access to current state and when you have data requirements
> about what type of information you can store. They are also easy to present to a user
> for manual editing. Collections are useful when you want to track knowledge across many
> interactions without loss of information, and when you want to recall certain
> information contextually rather than every time."

The platform docs add the asymmetry Strata should recognize: a collection "tends to lead
to higher recall downstream", at the cost of moving complexity into the update and the
search (https://docs.langchain.com/oss/python/concepts/memory, "Collection"). Strata's
tier-3 is a collection with a profile-like pinned block — i.e. it already runs both arms,
and its measured "bigger budget = zero gain" result is evidence about exactly this
trade-off, which LangMem has no measurement of.

### Episodic memory

No built-in type. The guide defines an `Episode` schema the user must write
(`docs/docs/guides/extract_episodic_memories.md:19-43`), and the launch blog is explicit:
"LangMem doesn't yet support opinionated utilities for episodic memory"
(https://www.langchain.com/blog/langmem-sdk-launch). Episodic retrieval is DIY: the
guide's example does `store.search(("memories","episodes"), query=messages[-1], limit=1)`
and pastes the winner into the system prompt as few-shot guidance
(`extract_episodic_memories.md:111-136`).

### Procedural memory

Lives in a different place entirely: `Prompt` objects
(`src/langmem/prompts/types.py:7-33`) with `name`, `prompt`, `update_instructions`,
`when_to_update`, mutated by `create_prompt_optimizer(model, kind=...)`
(`prompts/optimization.py:50-58`). Persistence is the same `BaseStore` (the docs' pseudo-code
puts instructions at namespace `("agent_instructions",)`, key `"agent_a"` —
https://docs.langchain.com/oss/python/concepts/memory, "Procedural memory"), but there is
no store-backed prompt manager class in the repo; you wire it yourself.

### Mapping to Strata tier-3

Strata has **one flat fact type** (~160-char rows) plus two orthogonal axes: `scope` and a
`class` shelf mark (17 in use, 18% "wild"). LangMem has **N types** (tool names) plus a
`scope`-equivalent namespace, and *no* classification axis at all — its "type" is a schema,
not a subject taxonomy. Concretely:

| Concept | LangMem | Strata tier-3 |
| --- | --- | --- |
| Fact shape | `Memory.content` (free text) or a user Pydantic schema | fixed short-fact row, ~160 chars |
| Type marker | `kind` = Pydantic class name (`extraction.py:1114`) | `class` column, LCC-style shelf mark (`serve/memory.py:67`) |
| Tenant/scope | namespace tuple, templated (`utils.py:15-91`) | `scope` column (`serve/memory.py:79`) |
| Provenance / trust | store `created_at`/`updated_at` only | `source=ingest-*` / curation / journal + confidence |
| Validity | hard delete (`store.delete`, `extraction.py:1130`) | `invalid_at` / `superseded_by` |
| Recall-time type use | none (no router, no kind filter) | filters exist, no query→shelf router |

Strata's **trust axis is the real analogue of LangMem's typing**: LangMem separates memory
by *shape* (which tool emitted it), Strata separates it by *epistemic status* (who said it
and how much it should be believed). Those are different axes and Strata picked the one
that changes ranking; LangMem's shape-typing changes only what the extractor is allowed to
emit.

---

## Write path: extraction, consolidation, upserts

### The stateless core: `MemoryManager` (`extraction.py:217-533`)

Input is `{messages, existing?, max_steps?}` (`extraction.py:68-70`). The flow
(`ainvoke`, `extraction.py:237-339`; `invoke` is a line-for-line sync twin at
`:341-445`):

1. **Serialize the conversation**: `utils.get_conversation(messages)` merges runs and
   `pretty_repr`s each message (`utils.py:98-100`), wrapped in `<session_{uuid4}>` tags
   (`extraction.py:460-466`).
2. **Stamp existing memories** into `(id, kind, value)` triples so the model can patch by
   id (`_prepare_existing`, `extraction.py:480-507`). Bare strings are auto-boxed into the
   first schema (`:490-495`).
3. **Call trustcall's `create_extractor`** with one tool per schema and the toggles
   `enable_inserts` / `enable_updates` / `enable_deletes` /
   `existing_schema_policy=False` (`extraction.py:253-260`).
4. **Iterate up to `max_steps`** (default 1). From step 2 on, a `Done` tool is added
   (`:268-275`) and each tool result is echoed back as `"Memory {id} updated."` /
   `"deleted"` / `"inserted"` (`:299-327`). Unmentioned existing memories are re-injected
   unchanged (`:291-293`), so an omission is never an accidental delete.
5. **Filter the result**: removals of *internal* (newly-created) ids are dropped; removals
   of *external* (caller-supplied) ids survive as `RemoveDoc` for the caller to apply
   (`_filter_response`, `:509-533`).

### Where the conflict resolution actually happens: trustcall

LangMem does not implement upsert semantics itself. It delegates to
`trustcall.create_extractor` (pinned `>=0.0.39`, `pyproject.toml:12`), which turns the
update into **RFC-6902 JSON Patch operations against the retrieved documents**:

```python
# trustcall/_base.py:1280-1302
class PatchDoc(BaseModel):
    """Respond with JSONPatch operations to update the existing JSON document based on the provided text and schema."""

    json_doc_id: str = Field(..., description="First, identify the json_doc_id of the document you are patching.")
    planned_edits: str = Field(
        ...,
        description="Seconds, think step-by-step, reasoning over each required"
        " update and the corresponding JSONPatch operation to accomplish it."
        " Cite the fields in the JSONSchema you referenced in developing this plan."
        " Address each path as a group; don't switch between paths.\n"
        " Plan your patches in the following order:"
        "1. replace - this keeps collection size the same.\n"
        "2. remove - BE CAREFUL ABOUT ORDER OF OPERATIONS."
        " Each operation is applied sequentially."
        " For arrays, remove the highest indexed value first to avoid shifting"
        " indices. This ensures subsequent remove operations remain valid.\n"
        " 3. add (for arrays, use /- to efficiently append to end).",
    )
    patches: list[JsonPatch] = Field(...)
```

Deletion is a separate tool whose argument is **enum-validated against the ids that were
actually shown to the model** (`_create_remove_doc_schema`, `trustcall/_base.py:1192-1213`):
`"""Use this tool to remove (delete) a doc by its ID."""` with
`description=f"ID of the document to remove. Must be one of: {allowed_ids}"` and a
`field_validator` raising `ValueError(f"Document ID '{v}' not found. Available IDs: ...")`.

Validity is enforced by a **validate → re-patch retry graph**
(`trustcall/_base.py:402-418`, `_validation_node.py`): tool calls are run through Pydantic
without executing the tools, and a failure is fed back verbatim as a `ToolMessage` whose
text is `f"{repr(error)}\n\nRespond after fixing all validation errors."`
(`_validation_node.py:36-46`). The trustcall README states the rationale: "LLMs struggle
when asked to generate or modify large JSON blobs. trustcall solves this by asking the LLM
to generate JSON patch operations. This is a simpler task that can be done iteratively"
(https://github.com/hinthornw/trustcall).

**So LangMem's conflict resolution is: show the model the 5 nearest memories, force it to
address them by id, and guarantee that whatever it emits is *structurally* valid.** There
is no semantic gate: no similarity proposal stage, no subject veto, no containment test,
no "is the new row strictly richer" rule. Nothing in the code prevents a merge that is
syntactically perfect and factually wrong.

### The store-backed manager: `MemoryStoreManager` (`extraction.py:832-1663`)

Full write cycle (`ainvoke`, `extraction.py:1006-1137`):

1. **Read-before-write.** Either (a) `query_model` generates parallel `search_memory` tool
   calls — instructed "Queries should be formatted as hypothetical memories that would be
   relevant to the current conversation." (`:887-895`, payload text at `:1017-1020`) — or
   (b) no query model: embed **dilated windows** of the tail of the conversation, sizes
   1, 2, 4, 8, … messages (`utils.get_dialated_windows`, `utils.py:103-119`; called at
   `:1032-1034`).
2. **Merge + rank** all hits by `(namespace, key)` and keep the top `query_limit` (default
   **5**) by store score (`_sort_results`, `:991-1004`).
3. **Seed a profile** if nothing was found and a default exists: `store.aput(namespace,
   key="default", ...)` (`:1040-1063`).
4. **Map store keys to LLM ids** deterministically:
   `uuid5(NAMESPACE_DNS, str((*namespace, key)))` (`_stable_id`, `:936-938`) — the model
   patches real store keys, not opaque ones.
5. **Run `MemoryManager`** on `(messages, existing)` (`:1073-1080`), then optionally
   repeat over `phases` — each phase is another full manager pass over the current set,
   default instruction `"You are a memory manager. Deduplicate, consolidate, and enrich
   these memories."`, with `include_messages` optional (`MemoryPhase`, `:818-822`;
   `_build_phase_manager`, `:977-989`; loop at `:1086-1100`).
6. **Apply** (`_apply_manager_output`, `:940-975`; final diff at `:1102-1130`): unchanged
   documents are **not** re-written (`:1109`), new ids are `put` under the current
   namespace, and removed ids hit `store.delete` — **a hard delete** (`:1126-1134`).
7. `asyncio.gather` over all `aput`/`adelete` calls (`:1132-1135`).

Defaults worth noting: `create_memory_store_manager(..., enable_inserts=True,
enable_deletes=False, query_limit=5, namespace=("memories","{langgraph_user_id}"))`
(`extraction.py:1674-1678`) — **deletes are off in the public API by default**, so out of
the box a LangMem collection grows and contradicts rather than prunes. (The class
`__init__` defaults `enable_deletes=True` at `:845`, but the factory passes its own
`False` at `:2099`.)

`phases` is real code but **undocumented and unexercised**: it appears only in
`extraction.py` (lines `850`, `876`, `1086-1100`, `1229`, `1680`, `1720`, `2104`) and in
no doc, example, or test (`grep -rn "phases"` across `docs/`, `examples/`, `tests/`
returns nothing). It is nevertheless the closest thing in the repo to Strata's two-stage
"cosine proposes, law decides" pipeline — a second, message-free consolidation pass whose
sole job is dedupe.

### The hot-path tool: `create_manage_memory_tool` (`tools.py:25-356`)

Agent-driven writes, signature (as documented at `tools.py:64-68`):

```python
def manage_memory(content: str | None = None, id: str | None = None,
                  action: Literal["create", "update", "delete"] = "create") -> str: ...
```

Mechanics (`tools.py:271-303`): `delete` → `store.adelete(namespace, str(id))`;
`create`/`update` → `store.aput(namespace, key=str(id or uuid4()), value={"content": ...})`.
**Note the asymmetry with the background path: the hot-path tool replaces the whole
content string; it has no patch operation and no dedup check whatsoever.** Two guards exist
— `actions_permitted` can restrict the verb set (`tools.py:34-36`, `:264-268`), and
argument validation raises so the model can retry (`:283-291`).

The old `insertion_method="append"|"upsert"` knob is gone at 0.0.30, replaced by
`actions_permitted` + always-upsert-by-id semantics (`tools.py:297-303`). (Whether the
older spelling existed is in [Unverified claims](#unverified-claims).)

### Scheduling: `ReflectionExecutor` (`reflection.py`)

- **Local** (`LocalReflectionExecutor`, `:254-386`): a daemon worker thread +
  `PriorityQueue` keyed by `execute_at = time.time() + after_seconds` (`:328`), and
  **cancel-on-resubmit per `thread_id`**: a new `submit()` for the same thread sets the
  old task's `cancel_event`, cancels its `Future`, and the worker drops it
  (`:308-313`, `:414-420`). That is textbook debouncing: "If new messages arrive before
  then: 1. Cancel pending processing task 2. Reschedule with new messages included"
  (`docs/docs/guides/delayed_processing.md:47-53`, which recommends 30-60 minutes in
  practice).
- **Remote** (`RemoteReflectionExecutor`, `:143-251`): `runs.create(...,
  multitask_strategy="rollback", after_seconds=after_seconds)` (`:182-195`) — the
  LangGraph server does the debouncing, which is what you need in serverless
  (`delayed_processing.md:75-84`).

Strata's ~30-minute curation timer is the same *shape* of decision (off the hot path,
periodic), but LangMem's is **event-driven and self-cancelling**: it fires when a
conversation goes quiet, not on a wall-clock sweep, and it never processes a stale partial
thread.

---

## Read path & retrieval

There is no read pipeline object; retrieval is `BaseStore.search`, wrapped three ways:

1. **The agent tool** `create_search_memory_tool` (`tools.py:362-486`) exposes
   `search_memory(query, limit=10, offset=0, filter: dict | None)` (`tools.py:391-396`,
   `:436-451`) → `store.asearch(namespace, query=query, filter=filter, limit=limit,
   offset=offset)`, serialized as JSON text back to the model. Note `_MEMORY_SEARCH_INSTRUCTIONS
   = ""` (`tools.py:359`): **the shipped search tool carries no guidance at all** about
   when or how to search.
2. **Multi-query fan-out**: `create_memory_searcher` (`extraction.py:695-816`) binds the
   search tool with `tool_choice="search_memory"` and prompts "Search for distinct memories
   relevant to different aspects of the provided context." (`:697`) plus a trailing user
   message (`:773`); the model emits several queries, they are `abatch`-ed
   (`:801-815`), results are deduped by `(namespace, key)` and sorted by `item.score`
   descending (`:786-799`).
3. **The manager's own read** (`MemoryStoreManager.search`/`asearch`,
   `extraction.py:1353-1418`, `:1513-1578`): `query`, `filter`, `limit=10`, `offset`,
   `refresh_ttl`; if the namespace is empty and a `default_factory` is configured, it
   synthesizes a default profile item on the fly (`:1408-1417`, `:1568-1577`).

Ranking is **store similarity only** — one pgvector cosine over the configured `fields` of
the JSON document, plus optional exact content filters
(https://www.langchain.com/blog/semantic-search-for-langgraph-memory: `"fields": ["text",
"summary"]`, `["$"]` for the whole doc, `index=False` on `store.put` to skip embedding).
There is:

- no lexical/FTS arm and therefore **no rank fusion** (Strata: FTS5 bm25 + `bge-small-en-v1.5`
  fused by RRF, k=60);
- no reranker (Strata: measured +0.040 on LongMemEval oracle);
- no recency/confidence/importance blend — the store's only signals are `created_at`,
  `updated_at`, and `score`;
- no read-path near-dupe suppression (Strata dedups at read with Jaccard ≥ 0.75);
- no pinned block or budget policy: injection is hand-written f-string in every example,
  e.g. `system_msg = f"""You are a helpful assistant.\n\n## Memories\n<memories>\n{memories}\n</memories>"""`
  (`docs/docs/hot_path_quickstart.md:56-62`) and the episodic variant
  (`extract_episodic_memories.md:123-133`).

The gap between intent and implementation is documented by LangMem itself:
`conceptual_guide.md:40` says "memory relevance is more than just semantic similarity.
Recall should combine similarity with 'importance' of the memory, as well as the memory's
'strength', which is a function of how recently/frequently it was used." **Nothing in the
repo implements importance or frequency.** The only hook is `refresh_ttl` on read
(`extraction.py:1322-1331`, `:1370-1371`), which refreshes a row's TTL on access — a
use-adjacent signal that is exposed but never used for ranking.

---

## Namespaces & scoping

`NamespaceTemplate` (`utils.py:15-91`) is the whole scoping system: a tuple of strings,
any of which may be `"{var}"`, resolved from `config["configurable"]` at call time (or
from the ambient LangGraph context via `get_config()`, `:74-77`), raising
`errors.ConfigurationError(f"Missing key in 'configurable' field: ...")` on a miss
(`:85-89`). Extraction of the variable name is `_get_key` (`:94-95`).

Documented patterns:

| Pattern | Example | Source |
| --- | --- | --- |
| Per user | `("memories", "{user_id}")` | `hot_path_quickstart.md:96-101` |
| Per assistant | `("memories", "{assistant_id}")` | same |
| Org → user | `("memories", "{org_id}", "{user_id}")` | `guides/dynamically_configure_namespaces.md:47` |
| By memory category | `("agent_smith","memories","{user_id}","preferences")` | `dynamically_configure_namespaces.md:58-60` |
| Profile | `("users", "{user_id}", "profile")` | `guides/manage_user_profile.md:90` |
| Triples | `("chat", "{user_id}", "triples")` | `guides/extract_semantic_memories.md:144` |
| Asymmetric read/write between agents | write `("memories","team_a","agent_a")`, read `("memories","team_a")` | `guides/memory_tools.md:125-131` |

Namespaces are **directories, not columns**: a search is scoped to one namespace (or a
prefix of one), and cross-namespace discovery is done by `store.search` with a content
`filter` dict (`{"type": {"$eq": "session"}}` style, as used in the commented-out graph-RAG
sketch, `src/langmem/graph_rag.py:57-61`). Privacy is a first-class argument: "In LangMem,
all memories are given a **namespace** … the extent of memory sharing is determined both
by privacy and performance needs"
(https://www.langchain.com/blog/langmem-sdk-launch).

Two operational caveats visible in the tracker:

- **#108 (open, 2025-08-08)**: "LangGraph 0.6.0 Context Upgrade breaks dynamic namespace
  injection … LangMem needs to update the variable injection to read from Runtime Context
  instead of `config["configurable"]`" — the templating mechanism is coupled to an
  upstream API that moved (https://github.com/langchain-ai/langmem/issues/108).
- **#114 (open, 2025-09-03)** and **#140 (open, 2026-02-04)**: users report writes
  succeeding but recall returning nothing (`create_search_memory_tool` not retrieving
  memories written by `create_memory_store_manager`), typically a store-index/namespace
  mismatch (https://github.com/langchain-ai/langmem/issues/140,
  https://github.com/langchain-ai/langmem/issues/114).

Contrast with Strata: one SQLite file, two axes (`scope`, `class`) as **columns**, so a
single query can span scopes/shelves, filter them, or ignore them, and the same rows are
visible to the UI, the recall path, and the audit tools. LangMem's isolation is
structural — good for privacy, bad for cross-cutting ranking, and it means "route the
query to a shelf" costs a *namespace choice per request* rather than a `WHERE class IN
(...)`.

---

## Evaluation tooling (what they actually measure)

**Short answer: nothing, inside this repo.** LangMem ships memory patterns and delegates
measurement to LangSmith/the developer.

Evidence (all from the clone):

- **No eval or benchmark directory.** `git ls-files | grep -i eval` → empty. Top-level
  contents are `docs/ examples/ src/ tests/ langgraph.json Makefile pyproject.toml
  README.md uv.lock`.
- **A dangling reference to an evals package**: `pyproject.toml` declares
  `[tool.uv.workspace] members = ["evals/gen"]`, but no `evals/` path is tracked in the
  repo. Either it was removed or it lives elsewhere (see
  [Unverified claims](#unverified-claims)).
- **Test inventory** (`tests/`):
  - `tests/short_term/test_summarization.py` — 15 `test_*` functions, and an async twin
    (`test_summarization_async.py`, 15). These are genuine unit tests of token-threshold
    trimming/`RunningSummary` bookkeeping.
  - `tests/test_docstring_examples.py` — **one** test function that AST-extracts
    ```` ```python ```` blocks from docstrings and executes them, with a hidden
    `# ```python setup` block per file (`:24-51`). Running it requires a live LLM and a
    live LangGraph server: `make doctest` boots `langgraph dev` via `langgraph-cli[inmem]`
    and then `pytest tests/test_docstring_examples.py -vvv -n auto` (`Makefile`, `doctest`
    target). This is an **example-correctness smoke test**, not a quality metric: it
    asserts the snippets don't crash, not that the memories are good.
  - `tests/cassettes/` contains **one** VCR cassette (`ed2e87ca-….yaml`).
- **CI runs no memory tests.** `.github/workflows/` has only `deploy_docs.yml` (lint docs
  → build mkdocs → deploy Pages) and `release.yml`.
- **No published benchmark numbers.** Neither README, the docs, nor the launch post states
  a LongMemEval / LoCoMo / BEAM score. Third-party numbers exist but are vendor-run:
  Mem0's comparison table reports LangMem at **58.1% judge accuracy (LoCoMo), 60 s p95
  latency, ≈130 tokens/query**, labelled "OSS playground; too slow for chat"
  (https://mem0.ai/blog/benchmarked-openai-memory-vs-langmem-vs-memgpt-vs-mem0-for-long-term-memory-here-s-how-they-stacked-up,
  2026-07-21, updated 2026-08-31). Treat as adversarial and methodology-opaque.

What they *do* instrument:

1. **Feedback as an input type, not a metric.** `AnnotatedTrajectory(messages, feedback)`
   where feedback may be `{"score": 0.9, "comment": …}` or `{"revised": "…"}`
   (`prompts/types.py:36-59`; `guides/optimize_memory_prompt.md:13-44`). The procedural
   optimizers consume that scalar/annotation to rewrite prompts
   (`create_prompt_optimizer`, `prompts/optimization.py:50-58`). So the only thing LangMem
   *measures* is a user-supplied satisfaction signal, and only for prompt improvement.
2. **Structural validity** as a hard gate — trustcall's `ValidationNode` + re-patch loop
   (`trustcall/_validation_node.py:47-70`, `_base.py:402-418`). This is a real, automated
   quality bar; it just says nothing about whether the memory is *true*.
3. **LangSmith tracing hooks** — `import langsmith as ls`, `@ls.traceable(tags=["langsmith:hidden"])`
   (e.g. `trustcall/_base.py:762`), `with_config({"run_name": "thread_extractor"})`
   (`extraction.py:182`). Observability plumbing, evaluation left to LangSmith.
4. **Qualitative precision/recall language** only (`conceptual_guide.md:38`,
   `:40`, `:177`; platform doc "over-inserting / over-updating").

For a measurement-first project, the takeaway is inverted: LangMem's *absence* of an eval
harness is the strongest argument for Strata's culture. Strata's
`bench/lme_harness.py`, `tools/lme_retrieval_ab.py`, `tools/m2_merge.py` (union-find
duplicate families, `groups()` at `tools/m2_merge.py:59`, plus `declarative()`/`meta()`
filters at `:39`/`:44`) are exactly the artifact LangMem does not have.

---

## Prompts worth stealing (quote them)

Strata writes its own prompts; these are the templates in the repo, verbatim, with the
ones worth copying marked.

**1. The default consolidation instruction — `extraction.py:185-206` (`_MEMORY_INSTRUCTIONS`,
used by both managers, `:223` and `:843`). Worth stealing: the three-step scaffold and the
"surprising vs persistent" retention rule.**

> "You are a long-term memory manager maintaining a core store of semantic, procedural, and
> episodic memory. These memories power a life-long learning agent's core predictive model.
>
> What should the agent learn from this interaction about the user, itself, or how it should
> act? Reflect on the input trajectory and current memories (if any).
>
> 1. **Extract & Contextualize**
>    - Identify essential facts, relationships, preferences, reasoning procedures, and context
>    - Caveat uncertain or suppositional information with confidence levels (p(x)) and reasoning
>    - Quote supporting information when necessary
>
> 2. **Compare & Update**
>    - Attend to novel information that deviates from existing memories and expectations.
>    - Consolidate and compress redundant memories to maintain information-density; strengthen
>      based on reliability and recency; maximize SNR by avoiding idle words.
>    - Remove incorrect or redundant memories while maintaining internal consistency
>
> 3. **Synthesize & Reason**
>    - What can you conclude about the user, agent ("I"), or environment using deduction,
>      induction, and abduction?
>    - What patterns, relationships, and principles emerge about optimal responses?
>    - What generalizations can you make?
>    - Qualify conclusions with probabilistic confidence and justification
>
> As the agent, record memory content exactly as you'd want to recall it when predicting how to
> act or respond.
> Prioritize retention of surprising (pattern deviation) and persistent (frequently reinforced)
> information, ensuring nothing worth remembering is forgotten and nothing false is remembered.
> Prefer dense, complete memories over overlapping ones."

Note the tension with Strata: this prompt *asks* for consolidation and generalization
("Synthesize & Reason … What generalizations can you make?"), which is precisely how an
LLM-only write path manufactures false merges. Strata's law keeps consolidation
deterministic and (per the brief) does not invent new facts at curation time.

**2. The extraction command wrapper — `extraction.py:457-478`. Worth stealing: the
subordinate framing and the attempt budget line.**

```python
return [
    {"role": "system", "content": "You are a memory subroutine for an AI."},
    {"role": "user", "content": (
        f"{self.instructions}\n\nEnrich, prune, and organize memories based on any new information. "
        f"If an existing memory is incorrect or outdated, update it based on the new information. "
        f"All operations must be done in single parallel multi-tool call."
        f" Avoid duplicate extractions. {session}")},
]
```

with `session = f"\n\n<session_{uuid4}>\n{conversation}\n</session_{uuid}>"` and, when
`max_steps > 1`, the appended "You have a maximum of {max_steps - 1} attempts to form and
consolidate memories from this session." (`:460-466`).

**3. The termination tool — `extraction.py:209-214`. Worth stealing: a cheap "am I done?"
signal that lets a drain loop stop early.**

> `class Done(BaseModel): """Only call this tool once you are done forming & consolidating
> memories. Before that, continue to refine existing memories by patching and removing them
> or create new ones."""`

**4. The patch planner — `trustcall/_base.py:728-738` (system-message suffix) plus the
`PatchDoc.planned_edits` field quoted above. Worth stealing: the op-ordering discipline and
"cite the fields you referenced".**

> "Generate JSONPatches to update the existing schema instances. If you need to extract or
> insert *new* instances of the schemas, call the relevant function(s)."
> followed by `<existing>\n<instance id=… schema_type="…">\n{doc}\n</instance>\n</existing>`

**5. Delete-by-id with an enumerated whitelist — `trustcall/_base.py:1195-1210`.** Worth
stealing verbatim: it makes a hallucinated `supersede_id` impossible rather than merely
unlikely.

**6. Hypothetical-memory search queries — `extraction.py:889`:**
`instructions="Queries should be formatted as hypothetical memories that would be relevant
to the current conversation."` (a HyDE-style query rewrite; worth stealing for the write
path's candidate retrieval).

**7. Multi-aspect search prompt — `extraction.py:697` + `:773`:**
`"Search for distinct memories relevant to different aspects of the provided context."` …
`"\n\nSearch for memories relevant to the above context."`

**8. When the agent should write — `tools.py:28-32`:**

> "Proactively call this tool when you:\n\n 1. Identify a new USER preference.\n 2. Receive
> an explicit USER request to remember something or otherwise alter your behavior.\n 3. Are
> working and want to record important context.\n 4. Identify that an existing MEMORY is
> incorrect or outdated."

and the generated description line (`tools.py:350-352`): "…{create, update, or delete} a
memory to persist across conversations. Include the MEMORY ID when updating or deleting a
MEMORY. Omit when creating a new MEMORY - it will be created for you."

**9. Standalone-context schema discipline — `docs/docs/guides/extract_semantic_memories.md:44-64`:**

> "Without context, memories can be ambiguous when retrieved later:
> `{"content": "User said yes"}`  # No context; unhelpful … Adding context helps the LLM
> apply memories correctly: `{"subject": "user", "predicate": "response", "object": "yes",
> "context": "When asked about attending team meeting"}`"

This is Strata's "well-written standalone fact" rule expressed as a schema field instead of
prose. The `context` field is a cheap provenance slot Strata currently keeps outside the
row text.

**10. Episodic schema — `docs/docs/guides/extract_episodic_memories.md:20-35`:**

> `"""Write the episode from the perspective of the agent within it. Use the benefit of
> hindsight to record the memory, saving the agent's key internal thought process so it can
> learn over time."""` with `observation` ("The situation and relevant context"),
> `thoughts` ("Key considerations and reasoning process"), `action`, `result` ("What
> happened and why it worked").

**11. Change-control discipline for prompt/procedural edits — `prompts/gradient.py:16-65`
(`DEFAULT_GRADIENT_PROMPT`) and `prompts/metaprompt.py:27-56` (`DEFAULT_METAPROMPT`).
Worth stealing the *ratchet* sentences:**

> "If the prompt seems to do well, then no further action is needed. We ONLY recommend
> updates if there is evidence of failures. When failures occur, we want to recommend the
> minimal required changes to fix the problem." … "Do not recommend generic changes that
> aren't clearly linked to failure modes." … "Otherwise, mark `warrants_adjustment` as False
> and respond with 'No recommendations.'" (`gradient.py:47-65`)
>
> "2. Recommend the minimal changes necessary to address any real failures. If the prompt
> performs perfectly, simply respond with the original prompt without making any changes."
> (`metaprompt.py:53`)

The gradient design itself is the second steal: split *diagnose* from *apply* into two
calls, with the diagnose step emitting `warrants_adjustment` + `hypotheses` +
`recommendations` and the apply step being a separate metaprompt
(`gradient.py:67-85`: "Respond with the updated prompt. Remember to ONLY make changes that
are clearly necessary. Aim to be minimally invasive"). And note the ratchet is *code*, not
only prose: `warrants_adjustment: bool` in the extraction schema (`gradient.py:139`) and
`if not final_response.warrants_adjustment:` short-circuits back to the original prompt
(`gradient.py:350`, `:389`). Strata's curation loop can adopt the same "no-op is a
first-class answer" contract and measure how often it fires.

**12. Phase-two dedupe instruction — `extraction.py:983-986`:** `"You are a memory
manager. Deduplicate, consolidate, and enrich these memories."` — invoked with
`include_messages=False` by default, i.e. a pure store-side consolidation pass.

**13. Summarization prompts — `short_term/summarization.py:23-49`:**

> `("user", "Create a summary of the conversation above:")`
> `("user", "This is summary of the conversation so far: {existing_summary}\n\nExtend this summary by taking into account the new messages above:")`
> `("system", "Summary of the conversation so far: {summary}")`

Incremental ("extend the running summary") rather than re-summarizing everything — the
`RunningSummary` dataclass exists precisely to avoid re-doing work
(`summarization.py:52-56`). Relevant to Strata's journal drain: extend-per-sweep instead of
re-reading the whole journal.

**14. Validation-retry wording — `trustcall/_validation_node.py:36-46`:**
`f"{repr(error)}\n\nRespond after fixing all validation errors."`

---

## What LangMem does better than Strata tier-3

1. **Patch, don't rewrite.** The model emits RFC-6902 ops against a specific document plus a
   `planned_edits` rationale, and the patch is applied by code. Mechanism:
   `PatchDoc` (`trustcall/_base.py:1280-1302`), patch prompt (`:728-738`), application in
   `_teardown` (`:762+`). Strata writes whole ~160-char rows, so every edit is
   all-or-nothing and the merge law must reason about complete texts. Patching makes
   "change only the date, keep the rest verbatim" expressible and diffable.
2. **Ids the model cannot hallucinate about.** `RemoveDoc.json_doc_id` is validated against
   the exact id set shown in the prompt (`trustcall/_base.py:1192-1213`), and the ids are
   derived from real store keys (`_stable_id`, `extraction.py:936-938`). A bad supersede
   target becomes a Pydantic error that triggers a retry (`_validation_node.py:47-70`)
   rather than a silent no-op write.
3. **A structural validity gate on every write.** validate → re-patch → validate graph
   (`trustcall/_base.py:402-418`), with tool calls validated *without executing* them so
   message/tool ids survive for multi-turn repair
   (`_validation_node.py:56-58`). Strata's curation loop has no equivalent retry-on-malformed
   stage that keeps the conversation intact.
4. **Read-before-write with LLM-authored queries.** `query_model` + `search_memory` tool
   with hypothetical-memory framing (`extraction.py:887-895`, `:1015-1029`), or dilated
   tail windows when no query model is set (`utils.py:103-119`, `extraction.py:1032-1034`).
   Strata's near-dupe guard proposes from one embedding of the new text
   (`serve/memory.py:502`); a HyDE-style candidate generator is a strictly larger
   candidate set for the same deterministic verdict.
5. **Bounded multi-step consolidation with explicit termination and feedback.**
   `max_steps`, the `Done` tool, and per-step `"Memory {id} updated."` tool messages
   (`extraction.py:266-334`). Strata's drain is one-shot per timer tick.
6. **Event-driven, cancellable scheduling.** Per-`thread_id` debounce with cancel-on-resubmit
   (`reflection.py:308-313`, `:328`, `:414-420`) and a remote mode with
   `multitask_strategy="rollback"` (`:192`). A quiet conversation triggers exactly one
   curation pass; a long-running one defers until it goes quiet.
7. **Clean separation of the pure transform from the applier.** `MemoryManager` is a
   side-effect-free `(messages, existing) -> [ExtractedMemory]` runnable
   (`extraction.py:217-455`; the docs call this the "functional core",
   `conceptual_guide.md:332-339`), and `MemoryStoreManager` is a thin store applier
   (`:1102-1135`). You can A/B the LLM step against frozen fixtures without touching a
   database. Strata's curation is coupled to `serve/memory.py`'s `MemoryService`.
8. **Multi-type extraction in one pass, with the type recorded per row.** Several Pydantic
   tools bound at once, `kind` persisted (`extraction.py:864-865`, `:1114`), and
   `existing_schema_policy` deciding what to do with documents of a type you didn't ask
   about (`trustcall/_base.py:157-160`: True=raise, False=treat as dict, "ignore"=drop
   patches). Cheap, explicit heterogeneity.
9. **A second-class-citizen but real consolidation-only pass** (`phases`,
   `extraction.py:818-822`, `:1086-1100`) that runs on the store contents *without* the
   conversation — the shape of a background dedupe sweep.
10. **Scoping that is enforced by construction.** Namespace resolution happens in one place
    (`utils.py:73-91`) and every stateful component takes a namespace template, so a
    mis-scoped write is a hard `ConfigurationError`. The read/write asymmetry pattern
    (write to `…/agent_a`, read from `…/team_a`, `guides/memory_tools.md:125-131`) is a
    clean multi-agent sharing story Strata has no equivalent of.
11. **Operational primitives inherited from the store**: TTL with refresh-on-read
    (`extraction.py:1322-1331`), per-item index opt-out (`index=False`, semantic-search
    blog), configured `fields` to embed, content filters with operators, and store-side
    versioning. Strata re-implements most of these by hand in SQLite.
12. **Procedural memory as a separate, feedback-driven loop.** `Prompt` + optimizers
    (`prompts/types.py:7-33`, `prompts/optimization.py:23-58`) close a
    trajectory→critique→rewrite cycle with a documented "don't touch it unless it failed"
    ratchet. Strata tier-3 has no learned-behavior store at all.
13. **Doc-as-test.** `make doctest` executes every docstring example against a live
    LangGraph server (`Makefile`, `tests/test_docstring_examples.py:24-51`), so the
    documented API cannot silently rot. Strata's docs have no such check.

Ranked honestly, #1-#4 are the ones with real expected value for Strata; #5-#13 are
architecture and ergonomics that Strata mostly doesn't need (single-user, single-GPU,
local-first).

---

## What Strata does that LangMem does not

1. **Deterministic merge law.** "Cosine proposes, a deterministic merge law decides":
   subject veto → payload containment → Jaccard drift → supersede-if-strictly-richer
   (Strata brief; implemented in `tools/m2_merge.py` and applied at write time by
   `MemoryService._semantic_dupe`, `serve/memory.py:502`, whose docstring records the
   measurement that forced the law: a duplicate family's own self-similarity spans
   0.59-0.92 while *different* facts sharing boilerplate sit at 0.95-1.00). LangMem has no
   semantic gate at all — the LLM's judgment is the merge decision
   (`extraction.py:1073-1080`), guarded only by schema validity.
2. **Measured false-merge rate.** 44% and 39% before the laws, with an offline audit tool
   building duplicate families by union-find (`tools/m2_merge.py:59-116`). LangMem has no
   equivalent measurement (see Evaluation tooling).
3. **Reversible history.** `invalid_at` / `superseded_by` keep the old row and its timeline.
   LangMem's store-backed path **hard-deletes** (`extraction.py:1130-1134`;
   `tools.py:294`), and its own docs concede the design point: "Since this uses the core
   'functional' API … you can control what 'removal' means, be that a soft or hard delete,
   or simply a down-weighting" (`guides/extract_semantic_memories.md:85`) — i.e. soft
   invalidation is left to you.
4. **Per-row epistemic metadata.** confidence, recency, provenance, and a trust axis
   (`source=ingest-*` vs curation vs journal). LangMem rows carry `kind` + `content` only;
   everything else is store bookkeeping (`created_at`, `updated_at`, version).
5. **Hybrid retrieval with rank fusion.** FTS5 bm25 + CPU `bge-small-en-v1.5` ONNX fused by
   reciprocal-rank fusion (k=60). LangMem: single-vector cosine + JSON equality filter
   (`BaseStore.search`), no lexical arm
   (https://www.langchain.com/blog/semantic-search-for-langgraph-memory).
6. **A reranker, measured (+0.040).** Nothing in LangMem re-ranks.
7. **A budgeted, pinned injection block** (96 notes / 24k chars, pinned per conversation)
   with a *measured* degradation curve (~3 points per store doubling) and a measured
   LongMemEval oracle of 0.761. LangMem's examples pick `limit=10` arbitrarily and never
   measure the effect of the block size.
8. **Read-path near-dupe suppression** so the block doesn't waste slots on restatements.
   LangMem dedupes only by `(namespace, key)` when merging multiple query hits
   (`extraction.py:786-791`, `:995-998`).
9. **Local-first, no cloud dependency.** Encrypted SQLite single file, vectors in the same
   DB, CPU-only pinned embedder (SHA-256-pinned 133 MB model). LangMem's persistence story
   is Postgres + pgvector, and every documented example uses a hosted embedding model
   (`openai:text-embedding-3-small`) and a hosted chat model.
10. **No per-write LLM in the hot path.** LangMem's hot-path mode is literally an LLM tool
    call per user message (`tools.py:25-356`, `hot_path_quickstart.md`), and even its
    background mode is per-message unless you add the debounce layer
    (`background_quickstart.md:82`, `delayed_processing.md`). Strata journals every turn and
    curates on a timer, off the request path.
11. **A measured cost/latency posture.** Everything runs on the one 24 GB GPU that also
    hosts the MoE LLM, with embeddings on CPU. LangMem's own third-party measurement is
    60 s p95 for a memory turn (Mem0 blog, above), and its docs assume a paid API.
12. **An operator UI and an agentic `memory_search` tool wired into the served model.**
    LangMem has the tool primitive (`tools.py:362-486`) but no UI, and its search tool ships
    with an empty instruction string (`tools.py:359`).
13. **Byte-identical default-path discipline** (opt-in features, default path unchanged) —
    `AGENTS.md` "A new feature is opt-in, and the default path stays byte-identical to the
    last release." LangMem is pre-1.0 and breaks its own API between minor versions
    (`insertion_method` → `actions_permitted`; issue #108 breaking namespace injection on an
    upstream bump).

---

## Portable ideas for Strata tier-3

Ranked by expected gain per hour, with the A/B harness named so each one is falsifiable.

### 1. Enum-constrained ids in the curation tool schema (hallucination-proof supersede)

- **Mechanism**: build the curation prompt's tool schema with the candidate ids as an
  enum, and validate the emitted `supersede_id`/`merge_into` against that set before the
  law runs; on failure, re-prompt with the Pydantic error (`trustcall/_base.py:1192-1213`,
  `_validation_node.py:36-46`).
- **Mapping**: the curation loop already receives candidate rows from
  `_semantic_dupe`/FTS; pass their ids as an enum in the tool JSON schema and reject
  anything else. Land it next to the law in `tools/m2_merge.py` + the write path in
  `serve/memory.py:401-502`.
- **Cost**: ~1-2 h. No new infra; one schema builder + one validator.
- **How we measure it**: replay the existing merge audits — count
  "proposed target id not in candidate set" before/after (should go to 0) and re-run
  `tools/m2_merge.py` on the same store copy to confirm the false-merge rate does not
  move. Then one `tools/lme_retrieval_ab.py` run with ≥3 repeats at temp 0.8/1.0 with
  `X-Strata-Memory: off` on the control arm.
- **Risk**: near zero. Worst case is more retry chatter and a slightly slower drain.

### 2. HyDE-style candidate generation for the write path

- **Mechanism**: before proposing a fact, ask the model (or a cheap local pass) to emit
  1-3 *hypothetical existing memories* and search with those instead of only the raw text
  (`extraction.py:889`, `:1015-1029`; dilated-window fallback `utils.py:103-119`).
- **Mapping**: today the near-dupe guard embeds the incoming text once
  (`serve/memory.py:502`). Add 1-2 extra FTS/vector probes from generated hypotheticals —
  off the hot path, so the extra LLM call is free.
- **Cost**: ~2-4 h (prompt + candidate union + dedupe of probe results).
- **How we measure it**: **merge recall@k** on the known duplicate families (the
  project's near-dupe family in rows 2550-2556 is a ready fixture; `tools/m2_merge.py`
  gives the families). Metric = fraction of true duplicate pairs where the older row
  appears in the candidate set at all. Target: raise candidate recall, then confirm the
  law's precision is unchanged.
- **Risk**: more candidates → more chances for the law to be *wrong*; the subject veto and
  containment checks must stay in front of the verdict. Guard by measuring precision on
  the audit families, not just recall.

### 3. Patch-style edits for aggregate/profile-like rows

- **Mechanism**: for rows that are conceptually slots (the pinned "restart-free store
  work" style notes, versioned policies, dated facts), let the model emit
  `[{op:"replace", path:"/date", value:…}, …]` plus a `planned_edits` rationale, and apply
  them in code with a JSON-Patch library
  (`trustcall/_base.py:1280-1302`; the platform doc notes profile rewriting "can become
  error-prone as the profile gets larger" —
  https://docs.langchain.com/oss/python/concepts/memory).
- **Mapping**: keep the merge law for *row-to-row* merges; add a patch path only for
  rows the curation loop marks as slot-shaped. Store the patch (not just the new text) as
  provenance.
- **Cost**: ~4-8 h (schema, applier, tests in `serve/test_memory.py`).
- **How we measure it**: **edit fidelity** — for a fixture set of N "fact changed" turns,
  count how often the new row preserves the non-target tokens exactly (character-level diff
  against the pre-edit row). Strata's current rewrite path almost certainly fails this on
  some fraction; that fraction is the win.
- **Risk**: medium. Patches on short 160-char rows can be more machinery than value;
  restrict to rows with a slot schema and measure before generalizing.

### 4. Two-layer split: pure `curate(messages, existing) -> ops` + separate applier

- **Mechanism**: LangMem's "functional core" (`conceptual_guide.md:332-339`;
  `MemoryManager` vs `MemoryStoreManager`, `extraction.py:217` vs `:832`) is a stateless
  transform plus a dumb applier.
- **Mapping**: extract the curation prompt+law into a pure function that takes journaled
  turns + candidate rows and returns ops, with the SQLite writes in a separate applier.
  Then the law and the prompt become testable against frozen fixtures, and the whole
  curation step can be A/B'd without a live store.
- **Cost**: ~4-6 h refactor, plus fixture authoring.
- **How we measure it**: it is an enabler, so measure it as *harness coverage*: number of
  curation behaviors with a deterministic offline test (0 → N), and time-to-run a curation
  prompt A/B (should drop to seconds, no GPU). Also assert byte-identical store output
  before/after the refactor on a fixed journal.
- **Risk**: low if guarded by the byte-identical-output check; medium churn in
  `serve/memory.py`.

### 5. Debounce the curation timer on conversation activity

- **Mechanism**: per-conversation pending task, cancel-and-reschedule on each new turn,
  fire after quiet (`reflection.py:308-313`, `:328`, `:414-420`; docs recommend 30-60 min,
  `delayed_processing.md:47-53`).
- **Mapping**: replace the fixed ~30-min sweep with a per-scope "fire 30 min after the last
  journaled turn, cancel if a new turn lands" job. Same timer budget, event-driven trigger.
- **Cost**: ~2-3 h (a small heap + a cancel key in the journal table).
- **How we measure it**: LLM curation calls per day (should drop on bursty usage), and
  **staleness**: time from a turn being journaled to its fact being recallable. Report both
  distributions; verify no regression in LongMemEval `multi-session` category on
  `tools/lme_retrieval_ab.py` (≥3 repeats, temp 0.8/1.0).
- **Risk**: medium — a long-lived conversation could defer curation indefinitely. Cap with
  a max-age (e.g. force a drain after 4 h regardless).

### 6. `Done` tool + bounded extra attempts for ambiguous drains only

- **Mechanism**: `extraction.py:209-214`, `:266-334` (multi-step with
  `"Memory {id} updated."` feedback and an attempt budget).
- **Mapping**: escalate to a second pass **only when the deterministic law returns an
  ambiguous verdict** (e.g. containment fails but cosine is high, or Jaccard sits in a gray
  band around 0.75). Everything else stays one-shot and law-decided.
- **Cost**: ~3-5 h (verdict routing + second-pass prompt + budget).
- **How we measure it**: false-merge / missed-merge rate **on the escalated subset only**
  (that is the population the second pass is for), plus total curation tokens per drain.
  Accept only if the escalated subset improves and curation cost stays within a stated
  multiplier.
- **Risk**: medium — this reintroduces LLM judgment into exactly the place Strata removed
  it. Mitigate by keeping the law as the final arbiter and using the second pass only to
  *choose candidates*, not to *approve merges*.

### 7. Use the `kind`/shelf axis as a real read filter (a query→shelf router pilot)

- **Mechanism**: LangMem persists `kind` per row (`extraction.py:1114`) and its search tool
  already exposes a `filter` dict the model can fill (`tools.py:391-396`, `:445-451`) —
  i.e. "the model chooses the scope predicate" is a first-class affordance.
- **Mapping**: let `memory_search` accept `class=[…]` (LCC shelf marks) and/or let the
  injected-block path derive a shelf predicate from the user turn. Strata already has the
  filters; it has no router.
- **Cost**: ~1 day for the tool-argument version (cheap); ~2-4 days for an automatic
  query→shelf predictor.
- **How we measure it**: per-category `turn_top1` on
  `tools/lme_retrieval_ab.py` — the existing A/B split by query type (the
  knowledge-update regression of -0.139 was found this way) — plus a
  **right-fact-in-block** delta, which is Strata's sensitive metric (it falls ~3 points per
  store doubling, so a router that shrinks the search space should move it).
- **Risk**: medium-high — a wrong shelf prediction silently excludes the right fact
  (recall loss with no visible error). Ship as a *soft* boost (demote other shelves) rather
  than a hard filter, mirroring the soft bounded demotion already chosen for the
  knowledge-update fix.

### 8. Steal the "minimally invasive / no-op is a valid answer" ratchet wording

- **Mechanism**: `prompts/gradient.py:47-65`, `:85`, `prompts/metaprompt.py:53` —
  "We ONLY recommend updates if there is evidence of failures", "Do not recommend generic
  changes that aren't clearly linked to failure modes", `warrants_adjustment=False`.
- **Mapping**: add a first-class `no_change` verdict to the curation loop's output schema,
  with the same "only with evidence, minimally invasive" prose, and log how often it fires.
- **Cost**: ~1 h (prompt + one enum value).
- **How we measure it**: fraction of drains emitting `no_change` (over-eager curation
  should show up here), and the false-merge rate on the audit families before/after. Both
  are already instrumented.
- **Risk**: low; under-firing could suppress legitimate updates — watch the recall side of
  the audit.

### 9. Frequency/strength signal via read-time touch

- **Mechanism**: LangMem exposes `refresh_ttl` on read (`extraction.py:1322-1331`) and its
  own guide says strength should be a function of recency and frequency
  (`conceptual_guide.md:40`) — it just never implements the ranking.
- **Mapping**: record `last_used_at` (and a hit count) when a row enters the injected block
  or is returned by `memory_search`; feed it into the existing blend.
- **Cost**: ~2 h (two columns + one UPDATE on the read path; mind the single-lock
  write pattern in `MemoryService`).
- **How we measure it**: blend A/B on `tools/lme_retrieval_ab.py` (≥3 repeats) with the
  frequency term as the only lever; also check that it does not create a rich-get-richer
  feedback loop by reporting Gini of `hit_count` over time.
- **Risk**: medium — a usage-boosted row can crowd out a newer, correct one. Bound the term
  the same way the demotion fix is bounded.

### 10. Procedural "how to behave" store (deferred)

- **Mechanism**: `Prompt` with `update_instructions`/`when_to_update` + gradient optimizer
  (`prompts/types.py:7-33`, `prompts/gradient.py`).
- **Mapping**: a small `class=PN`-style shelf (or separate table) holding behavioral rules
  learned from journal feedback, injected as part of the pinned block.
- **Cost**: ~1 week including the eval needed to gate it.
- **How we measure it**: this is a *different product surface* — it must be measured on
  response-quality judgments or a task benchmark, not on `turn_top1`. Strata's
  measurement protocol (temp 0.8/1.0, ≥3 repeats, memory muted on the control arm) applies,
  but LongMemEval will not detect it.
- **Risk**: high — prompt/rules drift is hard to attribute, and Strata's residual errors are
  ~70% reasoning failures with full evidence present, which a rules store may not fix.
  Park until the tier-3 items above are done.

---

## Search log

Web searches (Brave Search API, `brave_web_search`, count=10, 2026-10-14; 30 calls, 26
distinct queries). Result counts were 8-10 for all except query 23 (3 hits).

| # | Query | Key URLs taken from it |
| --- | --- | --- |
| 1 | `LangMem LangChain memory SDK semantic episodic procedural` | langchain.com/blog/langmem-sdk-launch; langchain-ai.github.io/langmem/concepts/conceptual_guide/; rywalker.com/research/langmem |
| 2 | `langmem extract consolidate index memory management patterns` | same conceptual guide; deepwiki.com/langchain-ai/langmem/2-memory-management |
| 3 | `LangMem background memory manager just-in-time memory manager` | langchain-ai.github.io/langmem/background_quickstart/ |
| 4 | `LangMem LangChain long-term memory SDK semantic episodic procedural` | agentpatternscatalog.org; atlan.com/know/long-term-memory-langchain-agents/ |
| 5 | `langchain langmem extract consolidate index memory` | deepwiki 2.1-semantic-memory (profile pattern, `extraction.py:543-544`); langchain-ai.github.io/langmem/reference/memory/ |
| 6 | `langmem memory types semantic episodic procedural` | conceptual guide (re-confirmed) |
| 7 | `LangMem LangChain long-term memory SDK semantic episodic procedural` (retry after empty-parse bug) | docs.langchain.com/oss/python/concepts/memory |
| 8 | `langchain langmem extract consolidate index memory` (retry) | conceptual_guide.md at github.com/langchain-ai/langmem/blob/main |
| 9 | `LangMem background memory manager just-in-time memory manager` (retry) | background_quickstart |
| 10 | `langmem create_memory_manager create_memory_store_manager upsert` | guides/extract_semantic_memories ("LangMem has two similar objects…"); issue #140; deepwiki 2-memory-management |
| 11 | `LangMem memory evaluation tooling benchmark LangSmith evaluate` | mem0.ai/blog/benchmarked-openai-memory-vs-langmem-…; docs.langchain.com/langsmith/evaluation; zenml.io/llmops-database/enhancing-memory-retrieval-systems-using-langsmith-testing-and-evaluation |
| 12 | `langmem profile vs collection pattern semantic memories` | conceptual_guide (profiles vs collections); docs.langchain.com memory overview |
| 13 | `langmem namespaces LangGraph store dynamic namespace configuration` | guides/dynamically_configure_namespaces; issue #108; deepwiki 7.2-namespace-organization |
| 14 | `LangMem when not to use memory layer agents` | docs.langchain.com/oss/python/concepts/memory; reddit r/LangChain "Why I stopped defaulting to LangMem"; towardsdatascience.com/a-practical-guide-to-memory-for-autonomous-llm-agents |
| 15 | `langchain blog LangMem SDK launch hot path background memory tradeoffs` | the launch blog (fetched in full) |
| 16 | `langmem create_prompt_optimizer metaprompt gradient procedural memory` | guides/optimize_memory_prompt; reference/prompt_optimization |
| 17 | `langmem ReflectionExecutor debounce after_seconds delayed processing` | guides/delayed_processing; issues #194, #196 |
| 18 | `langmem short term summarization middleware conversation summary` | reference/short_term; guides/summarization |
| 19 | `langmem repo no evals directory tests only docstring examples` | github.com/langchain-ai/langmem; docs.langchain.com/langsmith/evaluation-concepts |
| 20 | `langmem issue 140 create_search_memory_tool does not retrieve memories` | issue #140; issue #114 |
| 21 | `langmem insertion_method append upsert deprecated actions_permitted` | reference/tools (`actions_permitted`) |
| 22 | `LangMem production issues latency cost memory retrieval competing inference` | reddit r/LangChain 1toabbw; llms.blog agent-memory-engines comparison |
| 23 | `langmem "just-in-time" memory manager hot path background` | **no LangMem hit** — only the unrelated JIT-Mem paper (huggingface.co/papers/2609.27334, memorypapers.org, llmwatch.com) |
| 24 | `langgraph BaseStore postgres store semantic search content filter versioning` | langchain.com/blog/semantic-search-for-langgraph-memory (fetched); pypi.org/project/langgraph-store-postgres |
| 25 | `langmem LongMemEval LoCoMo benchmark score comparison mem0` | mem0.ai/research; mem0.ai/blog/ai-memory-benchmarks-in-2026; github.com/mem0ai/memory-benchmarks; opensourceaireview.com |
| 26 | `langmem managed memory API reflection langgraph platform "memory"` | reference/memory; launch blog |
| 27 | `langmem MemoryPhase multi-phase consolidation deduplication pass example` | no LangMem-specific hit (confirms `phases` is undocumented); mem0 discussion #4787 |
| 28 | `trustcall JSONPatch extraction validation retry loop "trustcall"` | github.com/hinthornw/trustcall; pypi.org/project/trustcall |
| 29 | `agent memory false merge duplicate detection deterministic rules vs LLM judgment` | arxiv.org/html/2606.01435v1 ("Don't Ask the LLM to Track Freshness"); mnemoverse.com/docs/library/agent-memory-deduplication; caura.ai/blog/harness-engineering-deterministic-memory; arxiv.org/html/2609.32256 |
| 30 | `langmem episodic memory few-shot example retrieval not supported opinionated` | launch blog; todatabeyond.substack.com; mem0.ai/library/agent-memory/semantic-memory |

Page fetches:

- https://www.langchain.com/blog/langmem-sdk-launch (2025-02-18) — memory-type table,
  "LangMem doesn't yet support opinionated utilities for episodic memory", the
  "when NOT to add semantic memory" argument, namespace/privacy framing, data-dependent vs
  data-independent recall.
- https://docs.langchain.com/oss/python/concepts/memory — profile-vs-collection
  precision/recall trade-off, "some models may default to over-inserting and others may
  default to over-updating … see Trustcall … consider evaluation (e.g., with a tool like
  LangSmith)", hot-path vs background trade-offs, procedural-memory store pseudo-code.
- https://www.langchain.com/blog/semantic-search-for-langgraph-memory (2024-12-05) — store
  `index` config (`embed`, `dims`, `fields`, `["$"]`), `index=False` opt-out, pgvector
  `PostgresStore`.
- https://mem0.ai/blog/benchmarked-openai-memory-vs-langmem-vs-memgpt-vs-mem0-for-long-term-memory-here-s-how-they-stacked-up
  — third-party LangMem numbers (58.1% LoCoMo judge accuracy, 60 s p95, ≈130 tokens/query).

GitHub reads (`github` MCP):

- `issue_read` on langchain-ai/langmem #140, #194, #196, #108, #114 (all open at time of
  writing).
- `search_repositories` for stars/forks/issues/dates.

Repo reads: full clone at `/tmp/opencode/research/langmem` plus the pinned
`trustcall-0.0.39` wheel at `/tmp/opencode/research/tc/`. Files read in full or in
relevant part: `README.md`, `pyproject.toml`, `Makefile`, `pytest.ini`,
`.github/workflows/deploy_docs.yml`, `src/langmem/__init__.py`,
`knowledge/extraction.py`, `knowledge/tools.py`, `utils.py`, `reflection.py`,
`graphs/semantic.py`, `graphs/prompts.py`, `graphs/auth.py`, `graph_rag.py`,
`prompts/optimization.py`, `prompts/types.py`, `prompts/gradient.py`,
`prompts/metaprompt.py`, `prompts/prompt.py`, `prompts/stateless.py`,
`short_term/summarization.py`, `tests/test_docstring_examples.py`,
`examples/standalone_examples/{README.md,custom_store_example.py}`,
`docs/docs/{concepts/conceptual_guide.md, background_quickstart.md, hot_path_quickstart.md,
guides/{extract_semantic_memories, extract_episodic_memories, manage_user_profile,
memory_tools, delayed_processing, dynamically_configure_namespaces, optimize_memory_prompt}.md,
reference/{memory,tools}.md}`.

Tooling note (for the next researcher): `brave_web_search` in this environment returns a
**newline-delimited JSON string**, not a parsed array — `JSON.parse` on the whole payload
throws, so parse line by line.

---

## Unverified claims

1. **`langmem/schemas.py` and a `langmem/memory/` package** (named in the research brief)
   do not exist at 0.0.30 (`find . -name "schemas.py"` → empty; `src/langmem/` has
   `knowledge/`, `prompts/`, `short_term/`, `graphs/`). Whether they existed in an earlier
   release (e.g. the 0.0.5rc series visible on PyPI) was **not checked** — the clone is
   shallow (`--depth 1`), so there is no history to inspect.
2. **`insertion_method="append"|"upsert"`** on `create_manage_memory_tool` is asserted here
   as "gone at 0.0.30". The current signature has `actions_permitted`
   (`tools.py:34-36`); the existence and exact spelling of the older knob was not verified
   against an old release.
3. **"maintains a versioned history of all changes"** (`extraction.py:1685`) is a docstring
   claim about the LangGraph store, not about LangMem. Whether `PostgresStore` retains
   readable prior versions (vs just a version counter) was **not verified**; the
   `BaseStore` reference docs were not opened. Treat Strata's `invalid_at`/`superseded_by`
   as the only history it can actually rely on if it borrows this design.
4. **The `evals/gen` uv workspace member** (`pyproject.toml`,
   `[tool.uv.workspace] members = ["evals/gen"]`) is not in the tracked file list. It may
   live in another branch, another repo, or be stale. Conclusion "LangMem has no eval
   harness" is verified *for this tree at this commit*, not for LangChain's whole
   evaluation story (LangSmith is a separate product).
5. **Mem0's LangMem numbers** (58.1% LoCoMo judge accuracy, 60 s p95, ≈130 tokens/query) are
   **vendor-run by a competitor**, with the harness, LangMem version, extraction model,
   judge, and retrieval depth for the LangMem arm unspecified in the post. Do not quote them
   in Strata docs without reproducing.
6. **The managed LangMem service** announced in the launch blog ("we are also launching a
   managed service that provides additional long-term memory results for free") — current
   status not checked; the repo's only remote-execution path is
   `RemoteReflectionExecutor` against a LangGraph server (`reflection.py:143-251`).
7. **The Reddit thread** "Why I stopped defaulting to LangMem…" (cost/latency anecdote) is
   anecdotal; not used for any claim above beyond "users report retrieval cost competing with
   inference".
8. **`phases` behavior**: implemented at `extraction.py:1086-1100` (async) and `:1229`
   (sync) but has no test, doc, or example. Whether a real multi-phase run improves
   deduplication is **unmeasured by LangMem** and would have to be measured by Strata if
   borrowed.
9. **Whether the docstring examples currently pass**: `make doctest` needs an LLM API key
   and `langgraph dev`; it was not run here. The claim is only about what the target does,
   not that it is green today.
10. **Cross-namespace search semantics** (prefix vs exact, `list_namespaces` behavior) were
    inferred from LangGraph docs/blog summaries, not from reading `langgraph/store/base`
    source. Relevant to idea #7 (shelf-as-filter) and worth confirming before building.
11. **Strata-side numbers** (LongMemEval oracle 0.761, reranker +0.040, ~3 points per store
    doubling, 44%/39% false-merge rates, 17 shelves / 18% wild, 96 notes / 24k chars) are
    taken from the research brief as stated; they were not re-derived in this pass.
