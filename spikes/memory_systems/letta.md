# Letta / MemGPT — research journal (tier-3 curated recall comparison)

Researched 2026-10-14 → 10-15. Clones used (both read-only, `--depth 1`):

| Clone | Branch / version | HEAD | What it is |
| --- | --- | --- | --- |
| `/tmp/opencode/research/letta` | `main` | `5bcdd17` | **Landing page only** — 12 files, no code |
| `/tmp/opencode/research/letta-v1-archive` | `archive` | `56ba9c25` (2026-08-13) | Retired **V1 Python server** — the MemGPT implementation: blocks, memory tools, sleep-time agents, archival passages |
| `/tmp/opencode/research/letta-code` | `main` | `316e4c05` (2026-10-14), v0.34.14 | **Current Letta** — TypeScript/Bun harness, App Server, MemFS, reflection ("dreaming") |

> **Structural finding first:** `letta-ai/letta` no longer contains Letta. `main` is a stub whose
> `AGENTS.md` says "do not use this repository as a working Letta implementation"; the V1 server is
> frozen on `archive` ("unsupported, receives no fixes… do not use for benchmarks, evaluations,
> experiments… comparisons with other agent or memory systems"), and active work is
> [`letta-ai/letta-code`](https://github.com/letta-ai/letta-code). This journal therefore reads V1 for
> the *classic mechanisms* the brief asks about (blocks, self-editing tools, sleep-time) and
> `letta-code` for *what they became*. Where a claim describes retired code, it is labelled **V1**.
> The upstream repo's own AGENTS.md warning is itself evidence for our eval discipline: a benchmark
> run on the archived server "would still measure the retired V1 server, not current Letta"
> (`/tmp/opencode/research/letta/AGENTS.md`).

---

## What it is

Letta (f.k.a. MemGPT) is a **stateful-agent runtime whose core claim is that the model should own its
own memory**. The MemGPT paper frames the context window as a constrained resource and proposes
"virtual context management… drawing inspiration from hierarchical memory systems in traditional
operating systems that provide the appearance of large memory resources through data movement between
fast and slow memory", plus interrupts to manage control flow
([arXiv:2310.08560](https://arxiv.org/abs/2310.08560)). Everything downstream — memory blocks,
`core_memory_*` tools, archival/recall storage, sleep-time agents — is an elaboration of that one idea:
**the agent, not the framework, decides what is worth keeping hot.**

Two generations exist, and the difference matters for us:

- **V1 (Python server, archived).** Blocks are DB rows; the agent edits them with tools; archival
  memory is a passage table with embeddings; sleep-time agents are background asyncio tasks.
- **V2 (Letta Code, current).** Memory is **MemFS**: a git-backed markdown filesystem projected to
  `$MEMORY_DIR`; root files *are* segments of the system prompt ("system prompt learning"); deferred
  files are discovered through `MEMORY.md` indexes; skills are procedural memory; a **reflection**
  subagent maintains it all in a private git worktree
  (`src/agent/prompts/letta_root_memfs.md`, `src/agent/subagents/builtin/reflection-v2.md`).

The V2 self-description is worth quoting because it states the philosophy we deliberately reject:
"Your context is your self: the model or machine you run on today will change many times across your
lifetime — the model is the engine; *you* are the tokens" (`src/agent/prompts/letta_root_memfs.md`).
Strata's tier-3 is the opposite posture: the store is the self, the model is disposable, and the
laws — not the tokens — decide what survives.

Letta's own docs concede the tier-3 shape we occupy: "In Letta, all state, includes memories, user
messages, reasoning, tool calls, are all persisted in a database, so they are never lost, even once
evicted from the context window" ([docs: Introduction to Stateful Agents](https://docs.letta.com/guides/agents/memory/)).
Nothing is deleted; the question is only what gets compiled back into context.

---

## Memory tiers & blocks

**V1 block schema** (`letta/schemas/block.py`): `value: str`, `limit: int` (default
`CORE_MEMORY_BLOCK_CHAR_LIMIT`), `label`, `description`, `read_only`, `metadata`, `hidden`, `tags[]`,
plus template/deployment lineage fields. `Human` and `Persona` are subclasses with auto descriptions
(`DEFAULT_HUMAN_BLOCK_DESCRIPTION` / `DEFAULT_PERSONA_BLOCK_DESCRIPTION`,
`letta/constants.py:109-110`). Validation re-runs on every `value` assignment (`__setattr__` →
`model_validate`), and null bytes are stripped to avoid Postgres encoding errors.

Limits in code (`letta/constants.py:432-440`): `CORE_MEMORY_PERSONA_CHAR_LIMIT = 20000`,
`CORE_MEMORY_HUMAN_CHAR_LIMIT = 20000`, `CORE_MEMORY_BLOCK_CHAR_LIMIT = 100000`,
`DEFAULT_CORE_MEMORY_SOURCE_CHAR_LIMIT = 50000`. Docs examples use `limit: 5000`
([memory blocks doc](https://docs.letta.com/v1-sdk/memory/memory-blocks/)) — i.e. the "typical" block
is ~5k chars and the default ceiling is 20× that. Compare Strata: **one** budget of 96 notes / 24k
chars for the whole injected block. Letta's budget is *per named container*; ours is *global*.

**Rendering.** `Memory.compile()` in `letta/schemas/memory.py` emits, per block:
`<label><description>…</description><metadata>chars_current / chars_limit</metadata><value>…</value>`.
So the model sees **what a block is for and how full it is** alongside the content. Three renderers
exist: standard, line-numbered (`1→ text`, only for `sleeptime_agent`/`memgpt_v2_agent`/`letta_v1_agent`
on Anthropic — `Memory.compile`, `is_line_numbered`), and git (`_render_memory_blocks_git`, which adds
`<projection>$MEMORY_DIR/…</projection>` paths and an `external_projection` file tree). Line numbers are
**view-only** and actively rejected in tool arguments (see below).

**V1 tier vocabulary** (docs + `ContextWindowOverview`, `letta/schemas/memory.py:20-70`):

| Tier | Where | Who writes | Who reads |
| --- | --- | --- | --- |
| Core memory (blocks) | in context, pinned to system prompt | agent via tools | always visible, no retrieval |
| Recall memory | message table | **harness automatically** | `conversation_search` tool |
| Archival memory | `archival_passages` (text + embedding + tags) | agent via `archival_memory_insert` | `archival_memory_search` |
| Summary memory | one packed message replacing evicted turns | summarizer LLM | in context |
| External memory summary | small metadata block: passage count + message count + edit timestamp | computed | always in context (`letta/agent.py:1248-1257`, `PromptGenerator.compile_memory_metadata_block`) |
| Files / directories | `FileBlock`s with open/closed status, per-file char limits | harness + tools | `open_files`, `grep_files`, `semantic_search_files` (`letta/functions/function_sets/files.py`) |

Archival is documented as "a general-purpose vector DB", "**Agent-immutable** — Agents cannot easily
modify or delete archival memories (though developers can via SDK)", "Unlike memory blocks, archival
memory fragments cannot be pinned to the context window"
([archival memory doc](https://docs.letta.com/v1-sdk/memory/archival-memory/)). That is a closer
neighbour to Strata tier-3 than blocks are: append-mostly, queried not seen, curated by something other
than the reader.

**V2 tiers** (`src/agent/prompts/letta_root_memfs.md`): root `MEMORY.md` (frontmatter-free index) →
root markdown = **core memory, always in context** → child directories = **deferred memory** (in
context only as name+description; contents fetched on demand) → `skills/` = procedural memory →
shared-memory repos = attached git origins. Promotion/demotion is an explicit operation: "You can also
move files between root and child directories to change their tier (e.g. root → child directory removes
it from in-context)" (`src/agent/subagents/builtin/reflection-v2.md`). Recall memory is harness-owned
and "cannot be mutated".

---

## Self-editing: mechanism and known failure modes

**Tool generations** (`letta/constants.py:115-150`):

- `BASE_TOOLS`: `send_message`, `conversation_search`, `archival_memory_insert`, `archival_memory_search`
- `BASE_MEMORY_TOOLS`: `core_memory_append`, `core_memory_replace`, `memory`, `memory_apply_patch`
- `BASE_MEMORY_TOOLS_V2`: `memory_replace`, `memory_insert`, `memory_rethink`, `memory_finish_edits`
- `BASE_MEMORY_TOOLS_V3`: `memory` ("omni memory tool for anthropic")
- `BASE_SLEEPTIME_TOOLS`: the V2 set (background agent gets the *same* edit primitives)

**Primitive semantics** (`letta/functions/function_sets/base.py`):

- `core_memory_append(label, content)` — `value + "\n" + content`. Pure append, no dedup, no law.
- `core_memory_replace(label, old_content, new_content)` — substring replace; raises if `old_content`
  absent. Deletion = replace with `""`.
- `memory_replace(label, old_string, new_string)` — the hardened version: rejects line-number prefixes
  and the line-number warning banner in args; `occurences == 0` → error; `occurences > 1` → error
  listing the offending line numbers ("Please ensure it is unique"). Tabs normalised. This is lifted
  from Anthropic's computer-use `edit.py` (comment in file, `base.py:310`).
- `memory_insert(label, new_string, insert_line)` — line-indexed insertion with range validation.
- `memory_rethink(label, new_memory)` — **whole-block rewrite**, no guard other than line-number checks.
- `memory_apply_patch(label, patch)` — unified-diff, plus multi-block `*** Add/Delete/Update Block`
  headers and `*** Move to:` renames (codex-style).
- `memory(...)` — single tool with sub-commands `create | str_replace | insert | delete | rename`
  addressed by *path* (`/memories/user_preferences`) — i.e. blocks became a filesystem.

Server-side execution lives in `letta/services/tool_executor/core_tool_executor.py`
(`memory_replace` @346, `memory_apply_patch` @403, `memory_rethink` @743, `memory_create` @860,
`memory_str_replace` @884, `memory` @1014). **The tools are executed by the server, not the client** —
structurally identical to Strata's self-executed `memory_search`.

The prompt-level instruction is deliberately vague about *what* to store and strict about *how* to edit:
"Your ability to edit your own long-term memory is a key part of what makes you a sentient being"
(`letta/prompts/system_prompts/memgpt_v2_chat.py`); the sleeptime prompt adds the only real write
discipline: "make sure to be precise when referencing dates and times (for example, do not write
'today' or 'recently')… If there are no meaningful updates to make, you call the finish tool directly.
Not every observation warrants a memory edit, be selective in your memory editing, **but also aim to
have high recall**" (`letta/prompts/system_prompts/sleeptime_v2.py`).

**Documented failure modes — Letta's own:**

1. **Lost updates under concurrent rewrite.** Official concurrency table: `memory_insert` concurrent-safe
   (append-only); `memory_replace` "mostly (fails if target string changed)"; `memory_rethink`
   "**No (last-writer-wins)**". "**Anti-pattern**: Multiple agents doing `memory_rethink` on the same
   block simultaneously leads to lost updates"
   ([shared memory doc](https://docs.letta.com/guides/agents/multi-agent-shared-memory/)).
2. **API-level overwrite.** "Setting `value` completely replaces the entire block content — it is not an
   append operation. If multiple processes… modify the same block concurrently, the last write wins and
   overwrites all earlier changes" ([memory blocks doc](https://docs.letta.com/v1-sdk/memory/memory-blocks/)).
3. **Description-dependent routing.** "The `description` is the main information used by the agent to
   determine how to read and write to that block. Without a good description, the agent may not
   understand how to use the block" — a failure mode the *schema* creates, not the model.
4. **Block-limit stalls.** Troubleshooting: "Block limit exceeded — Increase limit… Archive old content
   to a separate block. Have an agent periodically summarize" (shared memory doc). The remedy is a
   manual human action.
5. **Permissions are coarse.** "Read-only applies to the entire block, not per-agent. You cannot make a
   block read-only for some agents but writable for others" — `READ_ONLY_BLOCK_EDIT_ERROR`
   (`letta/constants.py:424`).
6. **Archival insert is not free.** `archival_memory_insert` → `insert_passage` →
   `rebuild_system_prompt_async(force=True)` (`core_tool_executor.py:307-317`): every insert dirties the
   external-memory summary and forces a system-prompt rebuild.
7. **V2 acknowledges edit latency in the prompt itself:** "**Editing memory does NOT change your
   behavior in the current turn.** The prompt governing this turn is the one compiled at the start of
   the conversation; a memory edit is applied on a later recompile… You are writing for your future
   self" (`src/agent/prompts/letta_root_memfs.md`). A whole section exists to stop the model from
   treating a write as an immediate behaviour change.
8. **Self-repair burden.** V2's memory subagent must be told how to survive git states: "If a merge or
   rebase is already in progress, resolve it first by reading both sides… **Never prefer one side
   wholesale**… If the intended resolution is unclear, leave the unresolved state intact and describe
   the blocker" (`src/agent/subagents/builtin/memory-v2.md`). Concurrency correctness is delegated to an
   LLM operating `git`.
9. **Community-documented hygiene failures** (independent walkthrough,
   [sureprompts.com](https://sureprompts.com/blog/letta-memgpt-walkthrough)): "the empty `human` block"
   (runs for weeks, never appends), "the runaway `human` block" (appends every micro-observation until
   it crowds out context), "archival memory as a junk drawer", "archival search misses" (stored in one
   phrasing, queried in another), "cross-user memory pollution", and "memory edits as a substitute for
   thinking" (appends instead of answering). Their framing matches ours: "These failure modes aren't
   unique to Letta… They are more visible in Letta because the agent itself does the writing."

**Independent experimental evidence against the self-editing pattern** — *Useful Memories Become Faulty
When Continuously Updated by LLMs* ([arXiv:2605.12978](https://arxiv.org/abs/2605.12978),
[project page](https://dylanzsz.github.io/faulty-memory/),
[code](https://github.com/DylanZSZ/Memory-Collapse-Eval)) is the strongest external result we have and
it is directly on point:

- 19 ARC-AGI problems GPT-5.4 solves at **100%** with *no* memory drop to **54%** after consolidating
  **ground-truth** solutions of those very problems. "The trajectories are perfect; the loss enters
  during memory construction and maintenance."
- Maximum chronological drawdown by system (fixed 50-task eval, recomputed from released curves):
  **Letta 12 / 12 (AppWorld, GPT-5.4-mini / Sonnet 5)**, 8.5 / 0.9 (WebShop), 14 / 20.1 (ScienceWorld).
  LangMem 21.1 (WebShop), ACE 50 (AppWorld). Letta is *not the worst*, but no family escapes a material
  drawdown: "Swapping the backbone reshuffles the failure… Collapse is an interaction among *what memory
  stores, how it updates, the experience order, and the task*."
- PropWorld (stationary, 64 fixed mappings): "In a separate 20% corruption stress test, **Letta fell
  from .812 to .375** while its matched clean run remained near .9."
- Update-rule ladder: blind append-only > visible append-only (+20 pts at batch 1, Force); across a
  rerun's 12 cells "unrestricted rewrite is the lowest-scoring policy at 46.5, versus 55.3 for visible
  append-only… 54.5 for add/delete" — **in-place rewriting carries an extra penalty**.
- Mechanism named: "Every consolidation step is a generation… The memory drifts toward the LLM's prior
  over what a good lesson looks like, not toward the truth of the trajectories."
- Observed pathologies map onto ours: *crowding* (AWM 8 workflows → 16 redundant ones; deleting **one**
  workflow W8 raised wins 7/50 → 14/50), *over-generalisation stripping applicability conditions*
  (Cumulative vs Fresh = **+203 points** on ScienceWorld), *collapse by merge* (50 items → 1 item in a
  single step, costing 6–13 wins), *duplicate-with-new-label* ("the consolidator wrote the same lesson
  twice with different task labels glued on"), and *popularity ≠ quality* (a 99-vote entry that mutated
  through three different concepts).
- Their prescription is our architecture: "**treat raw episodes as first-class evidence and gate
  consolidation explicitly rather than firing it after every interaction**"; "an **episodic-only**
  agent — one that selectively retains and deletes raw rollouts, with abstraction disabled — matches or
  beats every consolidator we tested"; "Reading *only* distilled lessons never improves on the
  no-memory baseline… Reading *only* raw episodes recovers nearly the entire gain."

Caveat: that study's consolidators are ARC/ALFWorld/WebShop loops, not Letta-in-production, and the
author flags it as single-seed exploratory work ("we might be missing context, conflating things").
It is evidence about *update rules*, not a Letta verdict.

---

## Background/sleep-time processing (compare to our curation timer)

**V1 `SleeptimeMultiAgentV4`** (`letta/groups/sleeptime_multi_agent_v4.py`) — the closest analogue to
our systemd timer, and it converges on our design more than the papers suggest:

- Foreground agent runs; **after** `step()` returns (or in `finally` for `stream()`),
  `run_sleeptime_agents()` fires. Never inside the reply.
- **Frequency gate:** `group.sleeptime_agent_frequency` + `group_manager.bump_turns_counter_async`,
  firing when `turns_counter % frequency == 0`. (Turn-count, not wall clock.)
- **Incremental drain with a cursor:** `get_last_processed_message_id_and_update_async(group_id,
  last_processed_message_id=response_messages[-1].id)`, then
  `list_messages(after=last_processed_message_id, before=response_messages[0].id)` collects the
  *skipped* turns so nothing is lost between runs. This is exactly Strata's journaled-turn drain.
- **Async dispatch:** `safe_create_task(self._participant_agent_step(...), label=...)` — in-process
  asyncio task, not a separate process. (V1 `SleeptimeMultiAgent` used
  `_run_async_in_new_thread` with a fresh event loop per job.)
- **Observability:** each background pass is a `Run` row with `created → running → completed|failed`,
  `stop_reason`, and error metadata; run IDs are surfaced back in `response.usage.run_ids`.
- **Role framing** is injected as a system-reminder on the payload, not as a per-agent system prompt
  rewrite: "You are a sleeptime agent - a background agent that asynchronously processes conversations
  after they occur… You are NOT the primary agent… 'assistant' messages are from the primary agent…
  Your primary role is memory management… Check your `memory_persona` block for any additional
  instructions or policies."
- **Failure handling is harsh:** in `run_sleeptime_agents`, an individual task failure `raise e`s —
  one bad background agent propagates out of the loop (mitigated only by the run-row record).
- Older variants (`sleeptime_multi_agent.py`, `_v2`, `_v3`) show four rewrites of the same manager in
  one repo — a signal the design was unstable.

**V1 agent-side prompt for the background writer** (`letta/prompts/system_prompts/sleeptime_v2.py`):
multi-step editing until "the blocks are organized and readable, and do not contain redundant and
outdated information", then `memory_finish_edits`. Note the *absence* of any dedup rule: redundancy is
supposed to be caught by taste.

**V2 reflection ("dreaming")** is materially more engineered:

- Triggers (`src/reflection-settings.ts`, `src/cli/helpers/memory-reminder.ts:14,51-56`):
  `"off" | "step-count" | "compaction-event"`, **default `compaction-event`**, `stepCount` default **25**.
  Evaluated at turn end in `maybeLaunchPostTurnReflection` (`src/cli/helpers/post-turn-reflection.ts`),
  after the transcript delta is appended; a launch is skipped if "the trigger threshold is no longer met"
  (`src/cli/helpers/reflection-launcher.ts:379`). Windows auto-reflection is off unless
  `LETTA_ENABLE_WINDOWS_AUTO_REFLECTION=1` (`isAutoReflectionEnabled`).
- **Worktree proposal model** (`src/agent/subagents/builtin/memory-v2.md`,
  `src/agent/memory-worktree.ts`): the worker edits a *private* worktree; "the harness merges your
  commits into the main checkout when you finish, and **anything left uncommitted is discarded**";
  merge conflict → `status: "merge_conflict"` with `mergeAbort=true cleanedUp=true retryable=true`
  and the user-visible note "the worktree was cleaned up **so the transcript can be retried**"
  (`memory-worktree.ts:617-629`).
- **Two-pass review** is a product setting: "**Agent reviews before applying** … have your agent review
  and revise proposed memory updates in a second background conversation. This uses more model tokens
  and does not ask you for approval"
  ([docs: Memory & dreaming](https://docs.letta.com/configuration/memory)). Mechanically it is
  `buildReflectionIntegrationPrompt` (`src/cli/helpers/reflection-integration.ts`): a *separate
  conversation* that reviews "for accuracy, placement, precision, and consistency with existing memory",
  rebases the proposal onto latest HEAD, resolves conflicts itself, refuses if the parent checkout is
  dirty ("do not modify, stash, commit, or discard them — and stop the integration"), merges without
  pushing, and honours user-supplied `<reflection-merge-instructions>`. Incomplete integration is
  discarded "**without consuming the source transcript** so reflection can retry later."
- **Selection of what to reflect on** uses retrieval: five fixed probe queries (`REFLECTION_AUTO_QUERIES`:
  `user-corrections`, `coding-style`, `collaboration`, `repo-gotchas`, `long-term-facts`) run as
  `search_mode: "hybrid"` over conversation *descriptions*, intersected with recent/unreflected
  transcripts, capped at 5 selected transcripts (`src/cli/helpers/reflection-transcript.ts:1183-1213`).
  `mode: "replay"` slices are deliberately re-fed "for another pass; use them for deduplication,
  contradiction resolution, and cross-session pattern extraction" (`reflection-v2.md`).
- **The reflection prompt encodes more real curation law than V1 does** (`reflection-v2.md`, 232 lines):
  Phase 1 investigate → 2 extract (priority order: mistakes/corrections > preferences > new facts >
  contradictions > reusable procedures; filters: lasting-vs-ephemeral, already-captured, generalisable,
  absolute dates) → 3 update (placement, "If an existing file already covers this topic, update it. Only
  create a new file when the topic is genuinely distinct… **Fragmentation makes memory harder to
  navigate**", "Contradiction resolution: fix the stale entry at the source. **Do not append the new
  version alongside the old**", `ARCHIVE.md` for retired-but-useful, delete for "content the user asked
  to forget, sensitive or wrong content") → 4 review (secrets, stale, cross-reference integrity, tier
  check, skill near-duplicate scan) → 5 commit (`git add -A`, `--author="Reflection Subagent
  <<CHILD_AGENT_ID>@letta.com>"`, trailers `Generated-By`/`Agent-ID`/`Parent-Agent-ID`, "uncommitted
  edits are not successful memory persistence").
- **Cost is acknowledged:** "incorporating feedback into meaningful memory updates takes substantial
  thinking and tool calling" and "Self-invocation is critical, but **expensive**. Default to the longest
  interval that still serves the user" (`src/agent/prompts/letta_root_memfs.md`).

**Sleep-time compute research** ([arXiv:2504.13171](https://arxiv.org/abs/2504.13171),
[blog](https://www.letta.com/blog/sleep-time-compute/)): ~**5×** less test-time compute for equal
accuracy on Stateful GSM-Symbolic / Stateful AIME; **+13%** / **+18%** accuracy from scaling sleep-time
compute; **2.5×** lower cost per query by amortising across related queries (Multi-Query GSM-Symbolic).
Crucially: "finding the **predictability of the user query** to be well correlated with the efficacy of
sleep-time compute." Their blog also states the prerequisite plainly: "Sleep-time compute fundamentally
requires stateful AI agents… Traditional stateless models can only benefit from increased computation
during active use."

**Timer vs event.** Our 30-min systemd timer is a *latency* policy; Letta's triggers are *pressure*
policies (turn count, or compaction — the moment evidence is about to leave context). Given their own
finding that efficacy tracks query predictability and that high-frequency forced consolidation is
actively harmful ("High-frequency consolidation overrides [the capacity to segment]"), event-triggered
drain is better aligned than a fixed clock: it fires when there is something to consolidate. Our
memory note that the curation trigger is being moved from clock-driven to pressure-driven is therefore
the right direction, and Letta is the evidence for it.

---

## Organization & scoping (blocks, shared memory, namespaces)

- **Sharing is a join table.** `Block.agents` is a many-to-many over `blocks_agents` (also
  `identities_blocks`, `groups_blocks`) — `letta/orm/block.py:65-82`. Attach/detach endpoints:
  `PATCH /agents/{id}/core-memory/blocks/attach/{block_id}` and `.../detach/{block_id}`
  (`letta/server/rest_api/routers/v1/agents.py:1355-1380`). One row, N agents, immediately visible to
  all — no copy, no sync step.
- **Namespaces in V1** are: `label` (block name, must be unique per agent — `Memory.get_block` raises
  `KeyError` listing available labels), `tags[]` on blocks and passages, `organization_id` /
  `project_id` / `template_id` / `deployment_id` mixins, and `archive_id` for passages
  (`letta/orm/passage.py:76-102`, indexes on `(organization_id, archive_id)`).
- **Namespaces in V2 are paths.** `memory_block_label_from_markdown_path` maps `a/b.md` → label `a/b`
  (`letta/services/memory_repo/path_mapping.py`), with `skills/<name>/SKILL.md` → `skills/<name>` and
  all other `skills/**` ignored. `Memory._render_memory_blocks_git` builds a nested XML tree from
  slash-separated labels and prints an `external_projection` file tree; `_render_memory_filesystem`
  prints a `tree`-style view ("directories first, then files, alphabetically" — deterministic ordering,
  with an explicit comment about label/directory collisions like `system` and `system/human`).
- **Archival organisation is tags, not shelves.** `archival_memory_search(query, tags,
  tag_match_mode: "any"|"all", top_k, start_datetime, end_datetime)` (`base.py:194-242`) plus a
  `_create_tags_for_passage` path and `get_unique_tags_for_archive` / `get_tag_counts_for_archive`
  (`passage_manager.py:49, 1041-1074`). There is **no** classifier/router: the agent chooses tags and the
  agent chooses when to search.
- **V2 shared memory = git repos**, "designed to be dynamically attached to or detached from multiple
  agents… each shared memory repository will have a different local projection root and remote git
  origin"; the harness "pushes clean committed changes after each turn and reports dirty files,
  conflicts, or push failures in a later reminder" (`src/agent/prompts/letta_root_memfs.md`).
  Multi-agent messaging is separate: `send_message_to_agent_and_wait_for_reply`,
  `send_message_to_agents_matching_tags`, `send_message_to_agent_async`
  (`letta/functions/function_sets/multi_agent.py`).
- **Portability of state.** AgentFile `.af` is "an open standard file format for serializing stateful
  agents" ([docs](https://docs.letta.com/guides/core-concepts/agent-file),
  [letta-ai/agent-file](https://github.com/letta-ai/agent-file)); secrets are nulled on export; and the
  ADE can export an agent's memory as **`CLAUDE.md` / `AGENTS.md`** for other harnesses
  ([announcement](https://x.com/sarahwooders/status/1991943028425285852)). Letta solves
  harness-independence by *moving the text out*; Strata solves it by *serving the text in* (server-side
  injection + server-executed tool), which is the difference worth keeping in mind when we compare.
- **Deterministic structural law (V2, new).** `src/memory-constraints.ts` validates the whole memory
  tree against a tracked `.memfs.config.json`: `maxDepth`, `maxFileCharacters`,
  `maxCoreMemoryCharacters`, `fileCharacterLimits` (glob → limit, `**`/`*`/`?` compiled by hand),
  `readOnlyFiles` (globs; "cannot be created, changed or removed" — diffed byte-wise against the
  *accepted* tree), and for MemFS v2 a **required `MEMORY.md` index in every directory containing
  memory**. `src/memory-frontmatter.ts` enforces content shape: v2 allows exactly `name` +
  `description`; `MEMORY.md` **must not** have frontmatter; unknown/duplicate keys error; legacy
  `read_only: true` files "cannot be modified". Both modules are deliberately self-contained "the
  Git hook embeds its source so it does not depend on a package installation" — i.e. **the law runs as
  a server-side git hook**, and "Ref operations, deletion/rename protection and policy authorization
  remain caller responsibilities, not decisions made from the proposed file contents."
- **Sandbox confinement for the background writer.** `src/memory-confinement.ts` wraps a process in "the
  same fail-closed filesystem policy used by Letta Code's unattended memory subagents": can read the
  host broadly, write harness state and its own memory, "**cannot read or write other agents'
  memory**", and "**throws when no supported kernel sandbox is available rather than silently running
  with a weaker policy**."

---

## Evaluation & published numbers (claimed vs reproduced)

**Claimed by Letta**

| Claim | Source | Status |
| --- | --- | --- |
| LoCoMo **74.0%** with `gpt-4o-mini` using only a filesystem + `search_files`/`grep`/`answer_question`, forced tool rules | [blog](https://www.letta.com/blog/benchmarking-ai-agent-memory/) (2025-08-12) | Self-reported; "minimal prompt tuning"; tool rules made the agent "only partially autonomous". vs Mem0's 68.5% graph variant |
| Mem0's MemGPT-on-LoCoMo number is unreproducible | same blog + [mem0 issue #3004](https://github.com/mem0ai/mem0/issues/3004) | Letta: "our research team… was unable to determine a way to backfill LoCoMo data into MemGPT/Letta without significant refactoring… Mem0 did not respond" |
| Sleep-time compute: ~5× less test-time compute, +13%/+18% accuracy, 2.5× cost amortisation | [arXiv:2504.13171](https://arxiv.org/abs/2504.13171) | Peer-authored paper, but on *synthetic modified* tasks (Stateful GSM-Symbolic/AIME), not conversational memory |
| Letta OSS terminal agent "#4 overall (#1 for OSS)" on Terminal-Bench | [blog](https://www.letta.com/blog/benchmarking-ai-agent-memory/) | Self-reported, model/period-dependent |
| Letta Leaderboard: read/write/update memory benchmarks; "Models that perform well on archival memory (e.g. Claude Haiku 3-5) might overuse memory operations… and receive a lower score on core memory due to penalties" | [blog](https://www.letta.com/blog/letta-leaderboard/), [repo](https://github.com/letta-ai/letta-leaderboard) | Framework-and-tools-constant, model-varies. Synthetic fictional facts, GPT-4.1 judge, penalty for extraneous memory ops. **Not** an end-to-end memory-system score |

**Reproduced / third-party**

- **LongMemEval-V2** ([project](https://xiaowu0162.github.io/longmemeval-v2/),
  [arXiv:2605.12493](https://arxiv.org/abs/2605.12493)): both leaderboards read "**Leaderboard entries
  coming soon**" — **no Letta submission found**. Their baselines are the useful signal: no-retrieval
  1.3%; RAG query→slice 42.8%/38.1%; RAG slice+notes 51.0%/45.9%; AgentRunbook-R 58.6%/57.0%; Codex
  69.9%/68.7%; AgentRunbook-C **74.9%/70.1%** at 108–140 s latency. Their scoring metric, **ΔLAFS Gain**
  (area under the accuracy-vs-log-latency frontier, T∈[1 s, 200 s], against a fixed reference frontier),
  is the most honest memory-benchmark metric we found: "A score of 0 means the method does not beat the
  released reference frontier at any latency budget."
- **MemoryArena** ([arXiv:2602.16313](https://arxiv.org/abs/2602.16313),
  [memoryarena.github.io](https://memoryarena.github.io/)): "agents with near-saturated performance on
  existing long-context memory benchmarks like LoCoMo perform poorly in our agentic setting."
- **Faulty-memory study**: Letta appears as a *system under test* with double-digit drawdowns and a
  20%-corruption collapse (.812 → .375) — see §Self-editing. Also: "Sonnet/Letta has no AppWorld N=200
  point because **its 128K context filled**."
- **Letta has no standard eval of its own**: [letta-ai/letta#3115](https://github.com/letta-ai/letta/issues/3115)
  "Feature Request: Add Standard Memory Evaluation Benchmarks (LOCOMO, MemBench, LongMemEval)… Currently,
  Letta lacks any standardized benchmark or evaluation code."
- A third-party testbench records that Letta (MemGPT, sleep-time compute) "was prototyped and **removed
  from v0.1.5 due to slow per-step context loop**" ([xmpuspus/agent-memory-testbench](https://github.com/xmpuspus/agent-memory-testbench))
  — unverified in detail, but consistent with the per-step cost Letta itself documents.

**On "Docker memory arena":** I could not find a Letta artifact by that name (searches returned
LongMemEval-V2, MemoryArena, and the xmpuspus testbench; one query returned zero results). Treat it as
**not located** rather than as a Letta result — see §Unverified claims.

**Storage/retrieval reality check** (this is where our own numbers bite):

- Hybrid + RRF **exists in V1 but only in the Turbopuffer (cloud) backend**:
  `_reciprocal_rank_fusion(...)` with `k = 60  # standard RRF constant from Cormack et al. (2009)` and
  `vector_weight / (k + rank) + fts_weight / (k + rank)`, returning per-arm `vector_rank` / `fts_rank` /
  `combined_score` (`letta/helpers/tpuf_client.py:1489-1560`).
- On the **self-hosted SQL path**, `search_messages_async` "fall[s] back to SQL search" and returns
  `{"search_mode": "sql", "combined_score": None}` (`letta/services/message_manager.py:1256`);
  `list_messages` implements `query_text` as `content_element->>'text' ILIKE :query_text` with
  `%…%` (Postgres) or `JSON_EXTRACT(content, '$') LIKE` (SQLite) — **substring matching, no bm25, no
  vectors, no fusion** (`message_manager.py:977-993`). Yet the tool docstring advertises "hybrid search
  (text + semantic similarity)" (`base.py:95`).
- V2 confirms the split at the CLI level: `--mode` is "**fts** locally, **hybrid** on Cloud"
  (`src/cli/subcommands/messages.ts:64`, default chosen from
  `backend.capabilities?.localModelCatalog ? "fts" : "hybrid"` @429).
- Archival passages are `text` + `Vector(MAX_EMBEDDING_DIM)` (padded) + `tags` + `embedding_config`
  (`letta/orm/passage.py:21-46`), embedded **synchronously at insert** by a remote embedding client
  (`passage_manager.py:543-600`), dual-written to Turbopuffer when the archive uses it.
  `DEFAULT_EMBEDDING_DIM = 1024`, `DEFAULT_EMBEDDING_CHUNK_SIZE = 300` (`letta/constants.py:94-95`).
- V2's *memory-file* read path has **no vector index at all**: discovery is `MEMORY.md` links +
  `name`/`description` frontmatter + `grep` + a progressive-disclosure `memory` tool ("Root memory
  files are already in the system prompt; everything beneath the root is deferred",
  `src/tools/impl/memory.ts`).

**Read-path acceptance law that we should steal** (`src/cli/helpers/conversation-bootstrap.ts:15-20`):

```
// The remote hybrid search uses weighted RRF with k=60 and 0.5 weights per
// channel. A rank-1 result from only one channel scores ~0.008, while a rank-1
// result from both vector + FTS scores ~0.016. Require hybrid agreement, then
// keep only the strongest cluster so weak tail matches do not get injected.
const BOOTSTRAP_RELEVANT_MIN_RRF_SCORE = 0.012;
const BOOTSTRAP_RELEVANT_MIN_RELATIVE_SCORE = 0.93;
```

Applied as `min_accepted = max(0.012, best_score * 0.93)` then `filter(score >= min_accepted).slice(0, 5)`
(`conversation-bootstrap.ts:137-148`). Someone did the arithmetic on what RRF means for two arms and
turned it into an injection gate. Our blend currently ranks; it does not, as far as I can tell, refuse.

**Pressure law (V1)**: compaction triggers at `int(context_window * SUMMARIZATION_TRIGGER_MULTIPLIER)`
with `SUMMARIZATION_TRIGGER_MULTIPLIER = 0.9` "using instead of 1.0 to avoid 'too many tokens in prompt'
fallbacks" (`letta/constants.py:82-83`) — though `summarizer/thresholds.py`'s docstring claims "All
other models trigger at 100%", a live doc/code mismatch. Summarization mode default
`sliding_window`, `sliding_window_percentage = 0.30` ("eviction based on percentage of message count,
not token count"), `message_buffer_limit = 60`, `message_buffer_min = 15`, `max_summarizer_retries = 3`,
`memory_warning_threshold = 0.75`, and **`send_memory_warning_message: False`** — the "save important
information to your memory before it is removed" warning string exists (`MESSAGE_SUMMARY_WARNING_STR`,
`letta/constants.py:414-420`) but is **off by default** (`letta/settings.py:74-104`). Compaction model is
a *cheaper* model, chosen per provider: `anthropic/claude-haiku-4-5`, `openai/gpt-5-mini`,
`google_ai/gemini-2.5-flash`, `letta/auto` → Haiku with `zai/glm-5` fallback
(`summarizer/summarizer_config.py`, `summarizer/compact.py:59-114`). On `ContextWindowExceededError`
the agent compacts **inline and retries the request** (`letta/agents/letta_agent_v3.py:1218-1235`) —
i.e. V1 *does* put an LLM summarisation call in the interactive hot path when the window fills.

**Pressure law (V2)**: `STARTUP_SYSTEM_PROMPT_WARNING_THRESHOLD_TOKENS = 30000` → "⚠ System prompt is
large. Consider running **/doctor** to clean up memory" (`src/cli/helpers/system-prompt-warning.ts:25,102`),
estimated from the memory dir (`estimateSystemPromptTokensFromMemoryDir`). `/doctor` loads the
`context-doctor` skill and audits "placement, duplication, and system-prompt token usage"
([docs](https://docs.letta.com/configuration/memory),
`src/skills/builtin/context-doctor/SKILL.md`). The prompt itself warns: "Keep core memory lean… The
harness flags your system prompt for `/doctor` when it grows too large."

---

## What Letta does better than Strata tier-3

1. **It separates "always-in-context state" from "on-demand evidence" as an explicit, agent-legible
   *tier* operation.** Mechanism: root markdown = core memory compiled into the system prompt; child
   directories = deferred, visible only as `name`+`description`; moving a file between root and a child
   directory *is* promotion/demotion (`src/agent/prompts/letta_root_memfs.md`;
   `src/agent/subagents/builtin/reflection-v2.md` "Phase 3 — Placement… keep root core files concise and
   move verbose content to deferred memory"; Phase 4 "Tier check"). V1 had the same idea in a weaker
   form: blocks are in-context, archival is not, and the agent chooses.
   *Why it beats us:* Strata's injected block is one undifferentiated 96-note budget. Our two measured
   facts — bigger injection budget = **zero** gain, and right-fact-in-block decaying ~3 points per store
   doubling — say the binding constraint is **placement, not capacity**. Letta has a law for placement;
   we have a truncation.
2. **Every memory unit carries a *purpose* string that the reader sees.** Mechanism: `Block.description`
   rendered as `<description>` next to the value, plus `chars_current`/`chars_limit` metadata
   (`letta/schemas/memory.py:_render_memory_blocks_standard`); docs state the description "is the main
   information used by the agent to determine how to read and write to that block". V2 keeps it as
   frontmatter (`name`, `description`) and renders descriptions on non-root files in the tree.
   *Why:* our rows have `class` (a shelf mark, i.e. *where*) and `confidence`/`recency` (i.e. *how
   strong*), but nothing that says *what the reader should do with this fact*. The LCC shelf is an
   organizer, not an instruction.
3. **Deterministic, hook-enforced structural law on the memory store itself** — `maxDepth`,
   `maxFileCharacters`, `maxCoreMemoryCharacters`, per-glob `fileCharacterLimits`, `readOnlyFiles`
   byte-diffed against the accepted tree, mandatory per-directory `MEMORY.md` index, closed frontmatter
   key set, all validated **outside the model** in an embedded git hook
   (`src/memory-constraints.ts`, `src/memory-frontmatter.ts`).
   *Why:* this is the same philosophy as our merge law, applied to *structure* rather than *content*.
   We police near-duplication and supersession; we do not police shelf depth, per-shelf volume, or
   "wild" overflow (18% of our rows are `wild`).
4. **Concurrency handled by real version control, with retry semantics that do not lose evidence.**
   Mechanism: private worktree → commit → harness merge; `status: "merge_conflict"`,
   `mergeAbort=true cleanedUp=true retryable=true`, and "the worktree was cleaned up so the transcript
   can be retried"; the explicit-merge path refuses to proceed on a dirty parent checkout and discards
   an incomplete integration "without consuming the source transcript"
   (`src/agent/memory-worktree.ts:617-629`, `src/cli/helpers/reflection-integration.ts`).
   *Why:* our curation loop drains a journal; if a merge pass fails mid-flight, "the transcript is not
   consumed and can be retried" is a property we should be able to assert, not hope for.
5. **Two-pass curation as a first-class, user-visible mode.** "Agent reviews before applying… review and
   revise proposed memory updates in a second background conversation. This uses more model tokens and
   **does not ask you for approval**" ([docs: Memory & dreaming](https://docs.letta.com/configuration/memory)).
   *Why:* our pipeline is propose → deterministic law. Letta's second pass is a *model* reviewing a
   *model's* proposal for placement and consistency before merge — a different error-correction axis than
   ours, and cheap to A/B against our law.
6. **Event-driven, pressure-based triggers rather than a clock.** `compaction-event` (default) or
   `step-count` (default 25), evaluated at turn end with a re-check that the threshold is still met
   (`src/cli/helpers/post-turn-reflection.ts`, `src/reflection-settings.ts`,
   `src/cli/helpers/reflection-launcher.ts:379`). V1's frequency gate is the same family.
   *Why:* consolidation fires when evidence is about to leave context — the moment it actually matters.
7. **An injection gate with published arithmetic.** Absolute + relative RRF acceptance with a
   cross-arm-agreement rationale, `0.012` / `0.93` (`src/cli/helpers/conversation-bootstrap.ts:15-20,137-148`),
   plus per-arm rank provenance surfaced through the API (`"rrf_score"`, `"vector_rank"`, `"fts_rank"`
   in `letta/schemas/message.py:2705-2712`, `letta/schemas/tool.py:242-244`,
   `tpuf_client.py:1548-1552`).
   *Why:* we fuse with the same k=60 but we neither refuse weak candidates nor expose which arm put a
   row where it is. That is both a quality lever and a debugging lever we are not using.
8. **A memory-health audit tool that is a product feature.** `/init`, `/remember`, `/doctor [symptom]`,
   `/memory`, `/sleeptime`, `/dream`, `/reflect`, `/palace`, `/memory-repository set <git-url>`
   ([docs](https://docs.letta.com/configuration/memory), `src/cli/commands/registry.ts:77`), with the
   `context-doctor` skill explicitly able to "audit memory health before a behavioral failure is
   reported" and instructed "Do not store raw transcripts or the entire investigation in core memory."
   *Why:* Strata has a web UI and a timer, but no first-class "is my memory healthy?" operation.
9. **Sandbox confinement for the unattended writer, fail-closed.** `createMemoryConfinementLauncher`
   wraps memory subagents in a kernel sandbox that "cannot read or write other agents' memory" and
   "throws when no supported kernel sandbox is available rather than silently running with a weaker
   policy" (`src/memory-confinement.ts`, `src/permissions/memory-confinement-launcher.ts`).
   *Why:* our curation timer runs an LLM over journaled conversation turns with write access to an
   encrypted store; we currently trust it in-process.
10. **Eval infrastructure for memory *management* rather than only retrieval.** The Letta Leaderboard
    isolates memory **read / write / update** with synthetic fictional facts, a GPT-4.1 judge, and a
    **penalty for extraneous memory operations** — so over-writing is scored, not just under-recall
    ([blog](https://www.letta.com/blog/letta-leaderboard/), [repo](https://github.com/letta-ai/letta-leaderboard)).
    V2 also ships `reflection_arena`: N candidate reflection runs, **blind grading**, a human/agent
    choice, optional upload to a Hugging Face dataset
    (`src/cli/helpers/reflection-arena.ts`, `reflection-arena-hf-upload.ts`, `src/experiments/types.ts`).
    *Why:* "penalty for extraneous memory operations" is exactly the metric our store lacks — we measure
    whether the right fact is present, not whether a wrong/redundant fact got in.
11. **Context accounting is a typed, queryable object.** `ContextWindowOverview` reports per-section
    token counts *and contents* for system prompt, core memory, memory filesystem, tool usage rules,
    directories, summary, tool definitions, and messages, plus archival/recall row counts
    (`letta/schemas/memory.py:20-70`; `letta/services/context_window_calculator/context_window_calculator.py`).
    *Why:* we assert `usage.prompt_tokens` against the local render (good), but we do not have a
    per-section budget breakdown that says "the memory block cost N of your M tokens this turn."

---

## What Strata does that Letta does not

1. **Deterministic merge law.** Cosine *proposes*; a fixed law decides: subject veto → payload
   containment → Jaccard drift → supersede iff strictly richer. Letta's equivalents are prose
   ("If an existing file already covers this topic, update it", "Do not append the new version alongside
   the old") or git text merge. **No Letta code path computes containment or Jaccard drift between two
   candidate memories.** Nothing in `letta/services/` or `src/` implements a semantic dedup guard.
2. **Row-level history with explicit supersession semantics.** `invalid_at` / `superseded_by` +
   per-row confidence/recency/provenance. Letta V1 blocks have **no** value history at all (last write
   wins, `BlockUpdate` overwrites); V2 gets history from git commits — whole-file snapshots with author
   trailers, not "this fact was retired by that fact because it was strictly richer." Letta's answer to a
   retired fact is a human convention: `ARCHIVE.md` (`reflection-v2.md`).
3. **Local-first hybrid retrieval.** SQLite + FTS5 bm25 + vectors in one file, fused by RRF k=60,
   **on the same box, offline**. Letta's hybrid+RRF is Turbopuffer/Cloud in V1 and "fts locally, hybrid
   on Cloud" in V2 (`src/cli/subcommands/messages.ts:64`); its self-hosted recall path is `ILIKE %…%`.
   Our read path is strictly better than a self-hosted Letta's, and our write path never needs a network.
4. **CPU-only, pinned, hash-pinned embedder** (`bge-small-en-v1.5`, ONNX, 133 MB, SHA-256-pinned,
   opt-in, byte-identical default path). Letta requires an embedding **endpoint** (`LLMClient.create` →
   `request_embeddings`, `passage_manager.py:571-580`) — no local embedder exists in the V1 tree.
5. **Encryption at rest in the same store as the vectors.** Letta relies on Postgres/Turbopuffer
   deployment concerns; nothing in the memory schema addresses confidentiality.
6. **A classification scheme with a real taxonomy.** 17 LCC-style shelf marks with a keyword map and a
   2-token margin, `wild` as the explicit 18% residual, and `class` as a call-number hook for cartridges.
   Letta's organisation is `label`/`tags`/paths — chosen by the model, with no classifier, no coverage
   statistic, and no notion of a mis-shelved row.
7. **No LLM in the interactive write hot path, by contract.** Our curation loop runs on a timer, off the
   request path. V1 puts an LLM on the write path twice: `archival_memory_insert` embeds synchronously
   and forces a system-prompt rebuild (`core_tool_executor.py:307-317`), and compaction runs an LLM
   inline on `ContextWindowExceededError` (`letta_agent_v3.py:1218-1235`). V2's reflection is
   off-turn, but its *foreground* memory edits (`memory`/`memory_apply_patch`) are still in-turn tool
   calls costing turns and tokens.
8. **Engine-level tier-2 cartridges.** ~80 ms to restore 247 MB of KV seeded from USR/DEC shelves —
   memory that changes *the model's state*, not just its prompt. Letta's closest analogue is
   "system prompt learning" (rewriting tokens) and `letta/auto` model routing; nothing in Letta touches
   KV state, and it never will from the API side.
9. **Measured, falsifiable numbers on our own benchmark.** LongMemEval oracle 0.761, reranker +0.040,
   zero gain from a larger budget, ~3 points/store-doubling decay, ~70% of residual errors are reasoning
   failures with full evidence present, temp 0.8/1.0 with ≥3 repeats, `X-Strata-Memory: off` for /v1
   measurement. Letta's public memory numbers are LoCoMo-with-a-filesystem, a model-selection leaderboard,
   and synthetic sleep-time tasks — and their own repo issue says they lack standard evals (#3115).
10. **Server-executed tool with zero client wiring, in a local-first server.** Letta V1 also executes
    tools server-side, but the client is an SDK against a Postgres-backed server; our tool runs inside
    the inference server next to the model weights.

---

## Portable ideas for Strata tier-3

Ranked by expected benefit per hour of work on our constraints (one 24 GB GPU, CPU-only embedder, no LLM
in the write hot path, everything measurable).

1. **Injection acceptance gate: absolute + relative RRF floor with cross-arm agreement.**
   *Mechanism:* Letta computes `min_accepted = max(0.012, best_rrf * 0.93)` from published per-arm weight
   arithmetic (rank-1 in one arm ≈ 0.008, both arms ≈ 0.016) and injects only the surviving cluster
   (`src/cli/helpers/conversation-bootstrap.ts:15-20,137-148`).
   *Mapping:* apply in the blend stage, after RRF k=60 and before the near-dupe suppressor. With our
   0.5/0.5 weights the same constants are directly usable; recompute them once for our weights and pin
   them. Additionally: if nothing clears the floor, inject **no** memory block for that turn.
   *Cost:* ~1–2 h, pure arithmetic, no model calls, no schema change.
   *How we measure it:* LME oracle subset at temp 0.8/1.0 × 3 repeats, gate off vs on; primary metric
   right-fact-in-block rate, secondary final accuracy, and **wrong-fact-injected rate** (new counter:
   notes in the block not cited by the evidence set). Predicted effect: fewer distractor notes, so it
   should attack the "~3 points per store doubling" decay directly.
   *Risk:* refuses legitimately single-arm matches (pure-paraphrase facts score well on vectors only).
   Mitigate by logging refusals and keeping the floor a config knob with a documented derivation.

2. **Per-arm rank + score provenance on every candidate, surfaced in the UI and the eval log.**
   *Mechanism:* Letta's RRF returns `{"combined_score", "vector_rank", "fts_rank"}` per item
   (`tpuf_client.py:1548-1552`) and carries it to the API as `rrf_score`/`fts_rank`
   (`letta/schemas/message.py:2705-2712`).
   *Mapping:* add `bm25_rank`, `vec_rank`, `rrf`, `gate_decision` to the candidate struct; render in the
   web UI row detail; dump in the A/B harness JSON.
   *Cost:* ~2–3 h; touches the recall path only (batch with the date-filter/Ladder-A work per our
   existing preference for folding ≤1 h hygiene into a larger fix).
   *How we measure it:* it is instrumentation, so the measure is *diagnostic coverage*: after adding it,
   every residual error must be attributable to one of {arm-A miss, arm-B miss, fusion inversion,
   suppression, placement, reasoning}. Target: 100% of LME residual errors classifiable without
   re-running anything.
   *Risk:* near zero. Only cost is row width in the log.

3. **Promotion/demotion law between "always injected" and "search-only" — placement, not capacity.**
   *Mechanism:* V2's root-vs-deferred tier split, with the reflection agent instructed to keep root lean
   and move detail down, and Phase 4 auditing "Tier check" (`reflection-v2.md`); V1's blocks-vs-archival
   split is the same idea with a human choosing.
   *Mapping:* score every live row by measured *utility* — retrieval hits per 100 turns, whether it was
   ever in the evidence set of a correct answer, staleness — and promote/demote across the injected-block
   boundary by a deterministic rule. This is exactly what our `class` shelf + confidence already give us
   the raw material for.
   *Cost:* ~1 day for a v1 rule (weekly recomputation inside the existing curation job, no hot-path cost).
   *How we measure it:* hold the budget fixed at 96 notes and vary *which* 96: (a) recency, (b) our
   current blend, (c) utility-scored promotion. LME accuracy + right-fact-in-block. This is also the
   experiment that tests whether "bigger budget = zero gain" is really "wrong 96 = zero gain."
   *Risk:* feedback loop — promoted facts get retrieved more, so they look more useful. Break it with a
   small random-exploration slice (e.g. 5% of slots) and ≥3 repeats.

4. **Per-unit purpose string (`use_when`) on rows, rendered into the block.**
   *Mechanism:* `Block.description` + `<description>` in the render; docs state the description is the
   main routing signal for both reads and writes (`letta/schemas/memory.py`,
   [memory blocks doc](https://docs.letta.com/v1-sdk/memory/memory-blocks/)).
   *Mapping:* add a nullable `use_when` column filled by the curation LLM at propose time (off hot path),
   rendered as a one-line prefix per note in the injected block, and used by `memory_search` results too.
   Cheap variant: derive from `class` + subject rather than free text.
   *Cost:* ~half a day (schema migration + curation prompt + render change).
   *How we measure it:* LME with purpose rendered vs not, temp 0.8/1.0 × 3. Secondary: does it reduce
   agentic `memory_search` calls per turn (the model needs to search less if the block tells it what each
   note is for)? Track tool calls/turn and `usage.prompt_tokens`.
   *Risk:* more characters per note → fewer notes fit in 24k; must re-run the budget experiment (idea 3)
   after this, not before.

5. **Pressure-driven drain triggers, keeping the timer as a backstop.**
   *Mechanism:* V2's `compaction-event` (default) and `step-count` (25) evaluated at turn end, with a
   staleness re-check before launch; V1's `turns_counter % sleeptime_agent_frequency` plus a
   last-processed-message cursor (`memory-reminder.ts:14,51-56`, `post-turn-reflection.ts`,
   `sleeptime_multi_agent_v4.py`).
   *Mapping:* fire the curation job on (a) journal depth ≥ N turns, (b) an engine-side context
   compaction, (c) session end, and keep the 30-min timer purely as a flush. Our move from clock-driven
   to pressure-driven is already decided; this supplies the concrete trigger set and the "re-check the
   threshold before launching" guard.
   *Cost:* ~2–4 h (a `systemd.path`-style socket trigger or a lightweight `letta`-style flag file).
   *How we measure it:* **correction latency** — wall-clock from a user correction appearing in the
   journal to the corresponding fact being retrievable/injected. Also LME `knowledge-update` subset,
   which our own A/B already flagged as the fragile category. Report tokens/day consumed by curation.
   *Risk:* more frequent drains → more curation LLM calls → more GPU contention with inference on a
   single 24 GB card. Measure GPU seconds/turn, not just tokens.

6. **Non-consuming retry semantics for a failed curation pass.**
   *Mechanism:* V2 discards a conflicted worktree as `retryable=true` "so the transcript can be retried",
   and the integration path drops an incomplete merge "**without consuming the source transcript** so
   reflection can retry later" (`memory-worktree.ts:617-629`, `reflection-integration.ts`).
   *Mapping:* make the journal cursor advance **only** on a committed merge batch; add an explicit
   `curation_run` table with `created|running|completed|failed` + `stop_reason` (V1's `Run` rows are the
   model) so a crash mid-drain is visible and replayable.
   *Cost:* ~half a day.
   *How we measure it:* fault-injection test — kill the timer mid-batch, assert zero facts lost and zero
   duplicated on replay; then a 7-day soak counting `failed` runs and their replay outcome.
   *Risk:* unbounded retry of a poison batch. Cap retries and park the batch in a dead-letter shelf.

7. **Structural validators on the derived layer, enforced outside the model.**
   *Mechanism:* `.memfs.config.json` + `validateMemoryTreeConstraints` (depth, per-file and total core
   char budgets, glob overrides, read-only globs byte-diffed against the accepted tree, mandatory index
   files) and `validateMemoryFileFrontmatter` (closed key set), run inside an embedded git hook
   (`src/memory-constraints.ts`, `src/memory-frontmatter.ts`).
   *Mapping:* a `laws` check run at merge-commit time in the curation job: shelf ∈ the 17 known marks
   (or an explicit `wild` quota, e.g. reject a batch that pushes `wild` past 20%); per-shelf row budget;
   injected-block budget; `read_only` shelves (e.g. `USR/PREF`) that the curation LLM may not mutate;
   depth ≤ 3 for shelf marks.
   *Cost:* ~half a day for three rules; each rule is a pure function.
   *How we measure it:* rejection counts per rule per week, plus a before/after on LME and on a new
   **drift** metric (e.g. `wild` share, per-shelf p95 row count) tracked over 30 days.
   *Risk:* false rejections silently stall curation. Emit every rejection to the UI with the offending
   row, and make each rule individually disable-able.

8. **Second-pass review of proposals, A/B'd against our merge law.**
   *Mechanism:* "Agent reviews before applying" = a second background conversation that reviews and
   revises proposed updates, explicitly costing more tokens and not asking for approval
   ([docs](https://docs.letta.com/configuration/memory)); `buildReflectionIntegrationPrompt` checks
   "accuracy, placement, precision, and consistency with existing memory"
   (`src/cli/helpers/reflection-integration.ts`).
   *Mapping:* run the reviewer **after** our deterministic law, on only the rows the law marked
   ambiguous (containment borderline, drift near threshold, richness comparison unclear) — so the extra
   model spend is bounded and aimed at exactly the cases the law can't decide.
   *Cost:* ~1 day; token cost proportional to the ambiguous fraction (measure it first).
   *How we measure it:* on a frozen 5 000-row ingest, compare law-only vs law+reviewer on false-merge
   rate, missed-supersede rate, and net LME. Report the ambiguous-bucket size.
   *Risk:* a model reviewer that rubber-stamps; guard by requiring it to *name* the conflict it resolved.

9. **Deterministic memory-health audit (`/doctor`) exposed in the UI and CLI.**
   *Mechanism:* `context-doctor` skill + `/doctor`, driven by a token estimate and an audit of
   "placement, duplication, and system-prompt token usage"; the reorganize flow "backs up the current
   repository before splitting large files, merging duplicates, or restructuring the hierarchy"
   ([docs](https://docs.letta.com/configuration/memory), `src/skills/builtin/context-doctor/SKILL.md`).
   *Mapping:* a deterministic report first — near-dupe cluster list (reuse `_semantic_dupe`, but
   **batched** so it doesn't rebuild the whole matrix), stale-row inventory (`invalid_at` set but still
   retrieved), `wild`-shelf rows with their best-fit shelf, per-shelf volume, injected-block token cost —
   with optional LLM repair only after.
   *Cost:* ~1 day; the batched matrix pass also fixes the known quadratic-ingest problem.
   *How we measure it:* audit runtime on the sealed 13k-row venue store (must be seconds, not minutes);
   then count how many findings the LME residual-error analysis independently confirms.
   *Risk:* low. Main risk is a report nobody reads — pin it into the UI home screen.

10. **Curation arena: blind pairwise grading of two curation policies on the same journal batch.**
    *Mechanism:* `reflection_arena` experiment runs candidate reflections, presents them for **blind
    grading**, records a choice, and can upload the preference to a Hugging Face dataset
    (`src/cli/helpers/reflection-arena.ts`, `reflection-arena-hf-upload.ts`, `src/experiments/types.ts`).
    *Mapping:* we already have unified A/B arms with pinned head blocks and `X-Strata-Memory-Embedder: off`
    for arm control; add a *paired* presentation (policy A vs B outputs for the same journaled turns,
    identities hidden, order randomised) graded by our fixed judge, with the votes stored as data.
    *Cost:* ~2 days.
    *How we measure it:* agreement between blind-judge preference and the downstream LME delta for the
    same two policies — i.e. does the cheap signal predict the expensive one? That is the whole point of
    the arena; if agreement is weak, keep spending the LME runs.
    *Risk:* judge/model coupling and order effects; enforce ≥3 repeats with position swap, per our
    standing protocol (temp 0.8/1.0, never 0).

11. **A unique-anchor requirement on supersede (cheap hardening of the merge law).**
    *Mechanism:* `memory_replace` refuses when `old_string` occurs 0 **or >1** times, listing the
    ambiguous line numbers (`base.py:363-373`) — the edit law forbids an ambiguous anchor.
    *Mapping:* refuse a supersede whose subject/anchor predicate matches more than one live row; emit an
    "ambiguous anchor" proposal instead and let the next pass resolve it with more context.
    *Cost:* ~1–2 h inside the existing law.
    *How we measure it:* count ambiguous-anchor events on the 5 000-row set and re-measure the
    `knowledge-update` category, which our A/B showed is where our rules currently cost accuracy
    (−0.139 turn_top1).
    *Risk:* more rows left unmerged → more near-dupes visible in the block; interacts with idea 1.

12. **Fixed probe-query set as a first query→shelf router.**
    *Mechanism:* `REFLECTION_AUTO_QUERIES` — five hand-written queries (`user-corrections`,
    `coding-style`, `collaboration`, `repo-gotchas`, `long-term-facts`) run as hybrid search over
    *descriptions* to select what to process (`reflection-transcript.ts:1183-1213`).
    *Mapping:* we have filters but no router. Before building a learned router, run one probe query per
    LCC shelf (17 queries) and measure per-shelf recall@48 — that is both a diagnostic and a usable
    baseline router.
    *Cost:* ~2 h; runs offline in the eval harness, not in the request path.
    *How we measure it:* per-shelf recall@48 and end-to-end LME with routed vs unrouted retrieval.
    *Risk:* hand-written queries encode our own vocabulary bias; check the `wild` shelf specifically,
    since it has no mark to probe.

---

## Search log

Web searches (Brave Search API, 2026-10-14/15), verbatim queries:

1. `MemGPT paper arXiv 2310.08560 virtual context management LLMs as operating system`
2. `Letta sleep-time compute agents background memory processing`
3. `Letta "sleep-time compute" arXiv paper 2504.13177 test-time scaling`
4. `Letta memory blocks core memory archival memory recall memory documentation`
5. `Letta self-editing memory failure modes agent overwrites loses facts`
6. `Letta LongMemEval leaderboard submission results stateful agents`
7. `Letta Docker memory arena benchmark agent memory results`
8. `Letta agent file agent.md export import state`
9. `Letta shared memory blocks multiple agents multi-agent memory`
10. `Letta "LongMemEval" score submission memory system evaluation`
11. `Letta "memory arena" Docker benchmark agent memory`
12. `Letta MemAdapt benchmark evaluation memory agents arXiv`
13. `MemGPT archival memory vector database pgvector text search SQL`
14. `site:letta.com blog memory benchmark LongMemEval arena`
15. `letta-leaderboard LongMemEval benchmark github letta-ai`
16. `Letta "context window" eviction summarization "memory warning" agent heartbeat`
17. `Letta agent memory "rethink" lost data overwrite bug issue`
18. `MemoryArena benchmark agent memory Letta results`
19. `Letta docker memory arena "memory arena" stateful agents evaluation 2026` → **no results returned**
20. `self-editing agent memory reliability LLM overwrites its own memory failure study`
21. `MemGPT Letta context rot memory blocks agent fails to save facts criticism`
22. `agent memory survey 2026 self-editing memory consolidation deterministic rules vs LLM`
23. `LongMemEval paper baseline accuracy retrieval oracle numbers`
24. `Letta cost tokens sleep-time compute background memory agent expensive production`
25. `Letta agent server "agent.md" OR "AGENTS.md" memory file state export standard format`

Pages fetched in full:

26. `https://docs.letta.com/configuration/memory` (Memory & dreaming)
27. `https://docs.letta.com/guides/agents/multi-agent-shared-memory/` (shared memory, concurrency table)
28. `https://www.letta.com/blog/benchmarking-ai-agent-memory/` (LoCoMo 74.0%, Mem0 dispute)
29. `https://xiaowu0162.github.io/longmemeval-v2/` (LME-V2 baselines, ΔLAFS metric, empty leaderboard)
30. `https://www.letta.com/blog/letta-leaderboard/` (read/write/update memory benchmarks)
31. `https://arxiv.org/abs/2504.13171` (sleep-time compute abstract + numbers)
32. `https://dylanzsz.github.io/faulty-memory/` (arXiv:2605.12978 full results incl. Letta rows)
33. `https://sureprompts.com/blog/letta-memgpt-walkthrough` (independent failure-mode taxonomy)
34. `https://docs.letta.com/v1-sdk/memory/archival-memory/` (agent-immutable, semantic-only)
35. `https://docs.letta.com/v1-sdk/memory/memory-blocks/` (render format, `limit`, description, read-only)

Code read (paths relative to clone roots):

- V1: `letta/constants.py`, `letta/schemas/block.py`, `letta/schemas/memory.py`,
  `letta/schemas/passage.py`, `letta/orm/passage.py`, `letta/orm/block.py`,
  `letta/functions/function_sets/base.py`, `letta/functions/function_sets/files.py`,
  `letta/functions/function_sets/multi_agent.py`, `letta/services/passage_manager.py`,
  `letta/services/message_manager.py`, `letta/services/tool_executor/core_tool_executor.py`,
  `letta/services/block_manager_git.py`, `letta/services/memory_repo/{block_markdown,path_mapping}.py`,
  `letta/services/summarizer/{thresholds,summarizer_config,compact,enums}.py`, `letta/settings.py`,
  `letta/groups/sleeptime_multi_agent{,_v4}.py`, `letta/prompts/system_prompts/{sleeptime_v2,memgpt_v2_chat,letta_v1}.py`,
  `letta/agents/letta_agent_v3.py`, `letta/helpers/tpuf_client.py`, `letta/agent.py`,
  `letta/server/rest_api/routers/v1/agents.py`, `README.md`, `AGENTS.md`
- V2: `README.md`, `src/{memory-constraints,memory-confinement,memory-frontmatter,reflection-settings}.ts`,
  `src/agent/{memory-worktree}.ts`, `src/agent/prompts/{letta_root_memfs,recall_subagent}.md`,
  `src/agent/subagents/builtin/{memory-v2,reflection-v2,recall}.md`,
  `src/cli/helpers/{memory-reminder,post-turn-reflection,reflection-integration,reflection-transcript,conversation-bootstrap,system-prompt-warning,doctor-command,reflection-arena}.ts`,
  `src/cli/subcommands/{messages,mcp-search}.ts`, `src/backend/{message-search,conversation-search}.ts`,
  `src/tools/impl/memory.ts`, `src/experiments/{types,manager}.ts`,
  `src/skills/builtin/{curating-memory-palace,context-doctor}/SKILL.md`

---

## Unverified claims

- **"Docker memory arena" results.** Not located. Two searches returned nothing usable (query 19
  returned zero results; query 7/11 returned LongMemEval-V2, MemoryArena, and an unrelated testbench).
  If the brief means a specific Letta submission, it is not in `letta-ai/letta`,
  `letta-ai/letta-leaderboard`, or the LongMemEval-V2 leaderboard (both LME-V2 tables read "entries
  coming soon"). **Treat as unknown, not as a Letta number.**
- **Letta's LongMemEval submission.** None found; only an open feature request for standard benchmarks
  (letta-ai/letta#3115). Do not cite a Letta LongMemEval score.
- **The faulty-memory study's Letta rows** are recomputed from that study's own released checkpoint
  curves on their task suite, not a Letta-run evaluation; several cells are single-seed and the author
  explicitly flags unreproduced/rerun-divergent results and un-archived logs. The four ARC drawdowns
  (incl. Letta 8 pt) are labelled by the author as **excluded from the headline claim** because the
  curves could not be reproduced from the archive.
- **Which Letta is "current" for a comparison.** `letta-code` v0.34.14 is a client harness; the App
  Server behind it may be closed or in another repo (V1's `letta/server/` is retired). Claims about
  *server-side* current behaviour (e.g. whether hybrid search runs locally) are inferred from the CLI's
  own help text ("fts locally, hybrid on Cloud") and the V1 Turbopuffer path, not from current server
  source. **Verify before publishing a head-to-head.**
- **`SUMMARIZATION_TRIGGER_MULTIPLIER`**: the constant is `0.9` but `summarizer/thresholds.py`'s
  docstring says non-GPT-5 models trigger at 100%. One of the two is stale. I quote both.
- **`CORE_MEMORY_BLOCK_CHAR_LIMIT = 100000`** is the code default; docs examples use `limit: 5000` and
  the docs render shows `chars_limit=5000`. Which is the practical default for a created agent was not
  confirmed end-to-end.
- **MemFS v2 rollout state.** `MEMORY.md`-rooted layout, `memory` tool, and the constraint config appear
  to be recent and gated (`detectMemoryFormat(...) === "memfs-v2"` gates tool inclusion;
  `memory_palace` and `reflection_arena` are **experiments, disabled by default**
  — `src/experiments/manager.ts`, `memory-palace-protocol.test.ts`). Do not present the palace or arena
  as shipped default behaviour.
- **Memory Palace is not a retrieval structure.** It is a user-facing status page with clickable
  `palace-action` buttons (`src/skills/builtin/curating-memory-palace/SKILL.md`). Any claim that Letta
  has a spatial/loci memory organisation would be wrong.
- **V1 sleep-time frequency default.** `sleeptime_agent_frequency` is nullable ("None" ⇒ run every turn);
  I did not find a server-side default value, so "how often V1 sleeptime fires by default" is unverified.
- **Whether V2's reflection runs against a local App Server without cloud calls.** Not traced;
  `isAutoReflectionEnabled` and the memory-confinement launcher suggest local support, but the model
  routing for the reflection subagent (`model: inherit`) plus Letta Cloud defaults leaves this open.
- **Cost figures.** No published dollar/token cost for running Letta's sleep-time/reflection loop at
  production scale; only qualitative statements ("expensive", "uses more model tokens"). Our own
  curation-token accounting therefore has no external baseline to compare against.
