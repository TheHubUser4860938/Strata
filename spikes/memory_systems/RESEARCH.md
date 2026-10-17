# External memory systems — research journal (tier-3 optimization input)

**Why this file exists.** Tier-3 (curated recall) is the part of Strata that is proven and measured:
LongMemEval oracle accuracy **0.761** (500 questions x 3 repeats, temp 0.8, the benchmark's own checker,
muted control), reranker worth +0.040 over lexical, injection budget worth +0.000, and a residual where
**244 of 346 wrong answer lines had every gold session already present** — reasoning, not retrieval. The
user's call for this workstream: tier-3 is "the proven one", so optimize it hard, and use the six systems
below as the inspiration set rather than inventing in-house.

**Method, stated so the depth is auditable.** One research pass per system: 10+ web searches each (their
docs, their papers, third-party comparisons, issues that reveal real failure modes) plus the actual code —
`git clone --depth 1` into `/tmp/opencode/research/<name>` and read the implementation, not the README.
Every claim in a system file carries a URL or a repo file path. Anything not confirmable in code or docs
goes into that file's `## Unverified claims` section rather than into the summary.

Per-system reports live beside this file:

| system | file | focus of its comparative question |
|---|---|---|
| mem0 | `mem0.md` | LLM-driven extract + ADD/UPDATE/DELETE/NOOP consolidation; vendor benchmark claims |
| Zep | `zep.md` | bitemporal validity (`valid_at`/`invalid_at`), edge invalidation, contested numbers |
| Graphiti | `graphiti.md` | incremental graph construction, hybrid rankers, the "no LLM at query time" claim |
| Letta / MemGPT | `letta.md` | self-editing memory blocks, sleep-time compute (their analogue of our curation timer) |
| LangMem | `langmem.md` | memory typing (semantic/episodic/procedural), consolidation prompts, evaluation tooling |
| Cognee | `cognee.md` | document spine: chunk -> section -> document provenance, ontologies, ECL pipeline |

Each ends with **portable ideas**, and each idea must state its cost (LLM calls per write / per read, RAM,
index work) and **how we would measure it** on existing instruments — `tools/lme_retrieval_ab.py --offline`
(~45 s, mechanism-level), the right-fact-in-block probes (`m4_shelf_nav.py`, `m4_position.py`), or the
LongMemEval venue (~80 min, outcome-level). An idea with no measurement plan does not enter the backlog.

## The filter every idea has to pass

Strata's constraints are what makes this a comparison rather than a shopping list:

1. **No LLM call in the interactive hot path.** Curation runs on a ~30-min timer. Several systems below
   spend an LLM call per write, per read, or both — that is a cost, not a feature, on a single 24 GB GPU
   already running a quantized MoE model.
2. **No cloud dependency, no extra database.** SQLite + FTS5 + vectors in the same file, CPU-only ONNX
   embeddings. Anything needing Neo4j/FalkorDB/Neptune or a hosted API has to be translated or dropped.
3. **Deterministic where judgment was measured to be wrong.** Strata's merge laws exist because LLM-judged
   consolidation had measured false-merge rates (44% and 39%) before the laws were added. Any "let the
   model decide" proposal arrives here already carrying that burden of proof.
4. **A claim is a measurement.** Opt-in, default path byte-identical, temp 0.8/1.0 never 0, >= 3 repeats
   for pass/fail, memory muted on /v1 probes, timings read from RESPONSES not logs.

## Cross-cutting finding 1: the published numbers in this field are not comparable

Measured on: web, 2026-10-14. This is the frame that keeps the six reports honest about their own tables.

- **Mem0**: claims **94.4% on LongMemEval** and 92.5% on LoCoMo, self-reported. An independent paper
  (arXiv:2603.04814) measured **49.0%** on LongMemEval for a Mem0 pipeline with a GPT-5-mini reader.
  - https://mem0.ai/blog/ai-memory-benchmarks-in-2026 · https://particula.tech/blog/agent-memory-frameworks-tested-mem0-zep-letta-cognee-2026
- **Zep**: its paper (arXiv:2501.13956) reports **94.8% on DMR** (gpt-4-turbo) / 98.2% (gpt-4o-mini) and
  **63.8% / 71.2% on LongMemEval** with gpt-4o-mini / gpt-4o. The DMR number sits on 60-message
  conversations and beats MemGPT by 1.4 points; the paper itself says the results "must be
  contextualized". Zep's **84% LoCoMo claim was re-scored at 58.44%** by an outside evaluator in
  getzep/zep-papers issue #5. Zep's own blog attacks Mem0's comparison in return ("Lies, damn, lies &
  statistics").
  - https://arxiv.org/abs/2501.13956 · https://github.com/getzep/zep-papers/issues/5 · https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/
- **The benchmarks themselves are audited-faulty**: a community audit found **6.4% of LoCoMo's answer key
  is wrong and its LLM judge accepts up to 63% of intentionally wrong answers**, and argues
  **LongMemEval-S "fits entirely in modern context windows, making it more of a context window test than
  a memory test"**. LoCoMo-Plus (arXiv:2602.10715) exists specifically because LoCoMo measures explicit
  factual recall rather than beyond-factual memory.
  - https://www.reddit.com/r/AIMemory/comments/1s1jlnd/serious_flaws_in_two_popular_ai_memory_benchmarks/ · https://arxiv.org/html/2602.10715v1
- **Where the honest comparisons land**: one September 2026 survey concludes "there is no longer a
  meaningful accuracy gap to break the tie" between the leading frameworks; another puts Zep+Graphiti at
  71.2% vs Mem0's 49% on LongMemEval — a 22-point gap between two claims about the same benchmark. The
  axis that IS moving in the vendor material is **cost**: tokens per retrieval call (~6,956 on LoCoMo for
  Mem0's 2026 algorithm) and latency vs full-context (median 9.87 s, p95 17.12 s).
  - https://mnemoverse.com/docs/library/ai-memory-solutions-2026-q3 · https://mem0.ai/blog/state-of-ai-agent-memory-2026 · https://zensation.ai/en/vergleich/agent-memory-frameworks

**What Strata should do with this.** (a) Never print 0.761 beside a vendor's 94.4% without the sentence
"sames-store, same-boot, same-judge A/B is the only comparison in this project that means anything" —
which is precisely the arm-B -> arm-C discipline (+0.040 measured inside one process, one store, one
vector set). (b) The "LongMemEval is a context-window test" critique is the argument for the **queued
S-split run (200 sessions/question)**: the benchmark that gates beta-1 cannot see the failure mode that
threatens the product, which is a growing store. The S-split is the out-of-sample check on our own scaling
law, not a bonus experiment. (c) Cost numbers (tokens per retrieval, latency vs full-context) are a
language these systems use and users understand; Strata has the equivalents measured (block 15.2k chars at
96/24k, ~2.3 s first turn, 52 ms lexical / 137 ms hybrid recall at 2,521 rows) and should state them the
same way instead of only stating accuracy.

## Cross-cutting finding 2: what the six systems have in common that Strata does not

Confirmed against code in all six reports, not inferred from READMEs:

- **An LLM call at write time.** mem0 (until v3 deleted it, leaving MD5 dedup — see finding 4), Zep and
  Graphiti (entity/fact extraction plus LLM-judged invalidation), Cognee (**23–24.6x corpus tokens and 2
  LLM calls per chunk**), Letta (the model edits its own memory), LangMem (extract + consolidate). Strata
  spends its LLM budget on a 30-minute curation sweep and keeps the write path model-free.
- **Validity as a pair of dates on every fact.** Zep/Graphiti carry `valid_at`/`invalid_at` (event time)
  beside `created_at`/`expired_at` (transaction time) and expose both as query predicates
  (`edges.py:271-289`, `search_filters.py:27-52`). Strata has only transaction time: `invalid_at is null`
  is hardcoded into recall (`serve/memory.py:654`), so "was this true on 14 March?" cannot be asked.
- **A subject/entity level above the fact.** Graphiti (entity nodes + communities), Zep (the same, in
  Graphiti), mem0 (a subject-shaped retrieval signal with an explicit hub penalty, `main.py:1807-1821`),
  Cognee (a graph over chunks). Strata's facts are flat: the `df<=3` subject is computed at write time
  for the merge veto and then **thrown away at read**.
- **A document spine with provenance.** Cognee keeps `doc_id` + chunk ordinal and enforces a **byte-exact
  tiling invariant** (concatenating chunks in order must equal the source, checked on read and after every
  plan, `incremental_chunking.py:53-66`). Strata stores verbatim sentences with `{file, n}` provenance but
  no document object, no offsets, no reassembly.
- **Typed memories** (semantic / episodic / procedural — LangMem, mem0's Platform) versus Strata's single
  fact row plus a `source` trust axis (`ingest-*` vs curation vs journal), which is the closest analogue
  and the one worth keeping.
- **Placement as an operation.** Letta's core-vs-deferred-vs-skills tiers carry a per-unit `description`
  telling the reader what the unit is FOR, and moving a fact between tiers is a first-class edit. Strata's
  96-note block is one undifferentiated budget with no purpose strings — which is the most plausible
  explanation for our own measured null result on budget size.
- **Query-side routing.** Cognee ships a rule-based no-LLM query router with an explicit safety rule
  ("never add an LLM call without an unambiguous signal", `query_router.py`). Strata has document->shelf
  classification and zero query-side routing — and today measured that routing into shelves does not pay
  at 2,655 rows (see the killed list).

## Cross-cutting finding 3: what Strata has that the six do not

Also confirmed in code, so it can be stated without hedging:

- At-rest encryption with the vectors **inside** the encrypted file; a single-file store with no external
  database (all six need Postgres/Neo4j/FalkorDB/Redis or a hosted API).
- A **deterministic merge law** where cosine only proposes. Zep's invalidation is date arithmetic but the
  *candidate choice* is LLM-judged, and the result is audited bad upstream: 41% of a 3,950-fact graph
  carries `invalid_at` with **3 of 4 audited wrong** (Graphiti #1728, independently reproduced at
  16.1-49.2%). Strata's laws exist because LLM-judged consolidation measured 44% and 39% false merges.
- **Server-side agentic recall** the model can call without any client wiring, plus a UI over the same
  store. Every one of the six makes the harness do the integration.
- **Tier-2 KV cartridges**: restoring engine state (~80 ms for 247 MB) instead of re-sending text. Nothing
  in the six has an equivalent; they all pay tokens per read (mem0 publishes ~6,956 tokens per retrieval
  call on LoCoMo).
- **A measurement protocol the vendor numbers mostly cannot be checked against**: temp 0.8/1.0 never 0,
  >= 3 repeats for pass/fail, one fresh conversation per ask, the benchmark's own checker prompt verbatim,
  a muted control that answered `idk` 1,478/1,500 times, and same-store/same-boot A/B attribution.
  Cognee publishes **no LongMemEval score at all** (BEAM 0.79 is 20 questions, one conversation,
  in-sample at 10M); Graphiti's only self-hosted reproduction gives recall 37.0% and 3.65 s per turn of
  ingest against our measured 2.8 ms for a guarded write.

## Cross-cutting finding 4: the cautionary tale that validates the hard parts of our design

mem0's v3 algorithm (April 2026) **deleted** the paper's ADD/UPDATE/DELETE/NOOP step: OSS `add()` is now
single-pass ADD-only with MD5 hash dedup as its only guard (`main.py:1022`, the update prompt is dead
code), and OSS scoring has **no recency, no confidence, no state** (`utils/scoring.py:60-118`).
Consequence, in their own tracker: stale facts are an open production bug (#4956, 4 closed community PRs),
TOCTOU duplicates (#5867, #6515), and a 32-day audit reporting **97.8% junk** in one deployment (#4573).
Consolidation, decay, temporal ranking and graph memory all moved to the closed Platform. Their docs
concede full-context beats Mem0 on LoCoMo (72.90 vs 68.44). Independently: Letta's self-editing
consolidation shows double-digit drawdowns and collapses **.812 -> .375 under 20% corruption**
(arXiv:2605.12978), whose recommendation is literally "treat raw episodes as first-class evidence and gate
consolidation explicitly" — which is what Strata's journal + law + timer architecture already is.

**So the two things this project was tempted to drop — the deterministic merge law and the model-free hot
path — are the two the field's most-starred system abandoned and the second-most-starred is measured
failing on.** That is worth writing down before the next "let the model decide" proposal arrives.

## Killed today by measurement, before any of it got built

The filter these had to pass is the same one the shelf experiment failed, so they go on the record as
killed-at-this-size rather than forgotten:

| idea | where it came from | what killed it |
|---|---|---|
| two-stage retrieval (cheap pass -> infer shelf -> re-search inside) | our own LCC design | oracle navigation scores **exactly equal** to the unfiltered baseline at all three sizes; the router is **net negative** (0.888 vs 0.919) |
| "more prenamed shelves" (denser cutters) | user proposal + Graphiti subclasses | 45 clusters all smaller than the block lift the oracle by 3 probes of 197 |
| a bigger injection block **on personal-fact rows** | arm C, then the dev-store probes | at the note-bound dev budget: +0.000 relevant facts at both query shapes and both arms. **The venue half of this row is no longer killed** — re-measured 2026-10-15 with `tools/m4_blockcov.py` after the stale-pin-cache bug was found: 192/48k shows 19-20 notes against 14-16 at 96/24k and raises gold-row coverage +0.019 hybrid / +0.047 lexical. Separating it from the reranker at the accuracy level is arm C1, back on the venue queue. |
| near-dupe guard fix as a retrieval win | Graphiti #1956 | the product cost is **0.21 slots per block (0.2%)**; the cap explains 0 of 90 violating pairs |
| rank-order work as an accuracy lever | the reranker's +0.139 rank-1 swing | measured twice, and the product path is the one that counts: `tools/lme_posrank.py` on the 1,500 filed arm-C lines shows **no monotone position effect** (rank 1 0.748 vs rank 2 0.835 vs rank 10 0.833; flat inside the easy types), and `k=1`'s +0.013 top1 moves block coverage by only +0.004 -> ~+0.003 accuracy. The synthetic `m4_position.py` pilot DID find ~19 points front-to-tail, but only in blocks with exactly one answer-bearing row — real for reader attention, not for the product. The agentic tool is where rank order pays. |
| speaker/assistant-echo demotion | the knowledge-update -0.139 | parked; still parked — `k=1` does not repair it either, and the positional probe is the test of whether order can pay at all. `tools/m4_consensus.py` then showed all 11 flips were the cosine arm's OWN rank-1 row, and that a lexical-weighted fusion recovers knowledge-update (0.486 → 0.611) only by paying overall top1 (0.517 → 0.506) and top3 (0.849 → 0.830) — a metric swap, so it does not ship. |
| **the subject / entity axis** — gather by subject instead of filtering by category, the strongest idea shared by two of the six systems | Graphiti's entity rollup + mem0's hub-penalised subject index (`main.py:1807-1821`) | `tools/m4_subject.py` on the venue copy: coverage at the block's own width (k=16) 0.936 → 0.933 (lenient) or unchanged (strict), and **only 4 of 470 questions moved at all**. The headroom was real (multi-session 0.893, temporal 0.920) and the gather did not touch it — below ~11k rows the fusion already surfaces what an entity index would go and get. |

## The seventh system: LCC, the one our own taxonomy is named after (2026-10-15)

Six external systems were studied to inform tier-3; the seventh was hiding in our own design vocabulary.
`lcc.md` in this directory is the study — read from the Library of Congress's own pages and from the
schedule PDFs (`LCC_Q2024TEXT.pdf`, `LCC_Z2024TEXT.pdf`, the class outlines), with every caption number
quoted verified out of the text files rather than from a summary.

Five mechanisms in LCC that our store lacks, each with a measurement already on the record next to it:

| LCC mechanism | What we do instead | Measured cost of ours |
|---|---|---|
| **Address and access are separate systems** (call number vs index terms + `Cf.`/`see` edges vs LCSH headings) | one `class` column doing both | 20.3% of rows have no address at all; unscoped contamination 41-84% |
| **Retire, never renumber** (`(667.6-.63)` obsolete numbers kept with a `see` reference) | a re-cut rewrites `class` on 575-863 rows | the tier-2 cartridge is valid only for its exact bytes, so every re-shelve invalidates a pin |
| **Literary warrant** (a caption exists because material exists, by approved proposal) | a keyword map written before the collection existed | 5 shelves wider than the block = 74% of the store; cosine re-cut beat it on reach (0.947 → 1.000 on POL/ARSA) |
| **Captions carry scope notes** (`Including …`, `Class here …`, `For X see Y`, `Cf. …`) | bare token lists in `CLASS_MAP` | an extractor's shelf choice is unfalsifiable; 18% of rows land in `wild` |
| **Sortable numbers, spans, adjacency** (`Z699+`, `E201-298`, `76.9.A-Z` Cutter expansion) | `class LIKE 'POL%'` — subtree only | no way to ask for a fact's neighbours; "more context per hit" is the one lever left at 0.936 coverage |

Two of them landed as code today, because the roadmap needed them anyway: `MEM/CRG.3.431` (M3 cartridge
banks) was **rejected by our own validator** — the regex allowed one dotted tail, so no Cutter or part
number could be stored, including the form `class_name`'s docstring advertises. It is legal now, and the
subtree predicate is boundary-exact in both arms (`QA76.9` no longer answers for `QA76.95`; SQL filter
measured 0.05 vs 0.04 ms on the 2,796-row store, i.e. free). Details in `docs/MEMORY_CORE.md`, "Shelf
isolation".

Two more are queued as experiments, not decisions:

- **Subjects table (many-to-many, LCSH-shaped).** Falsifier: route with headings instead of with the
  single shelf and compare against tonight's routed arm (leak 1.4-36.8%, reach unchanged). Fails if leak
  does not fall or reach moves.
- **Adjacency arm.** Fill the block's free slots by call-number neighbours of the top hits rather than by
  next-best score. Falsifier: rendered-block gold coverage (the metric that predicted accuracy at
  +0.048 → +0.040). Fails if coverage does not move — and it needs the document spine (backlog item 7) to
  mean anything, which is where the books-and-chapters design and LCC meet.

## Ranked enhancement backlog

Ranked by (measured or documented payoff) / cost, with the falsifier stated before the run. Items 1-4
attack the **reasoning residual** (244 of 346 wrong lines had every gold session present) or the
**temporal category** (0.465, the worst type); items 5-8 attack growth and hygiene; 9-10 are
instrumentation that makes everything else attributable.

**Re-ordered by what got measured the same night the list was written.** Item 1 is verified positive (facts
naming an absolute date: 0% → 75%, facts/turn flat, +3 chars, zero geometry voids). Item 3 — the subject
axis, the last open retrieval idea — is **measured no at this size**: gathering by shared rare-token subject
moved coverage on **4 of 470 questions** and lost 0.003 overall, so the fusion already surfaces what an
entity index would go and get below ~11k rows. Items 9 (rank provenance) shipped and paid for itself within
the hour. That leaves **presentation (item 4) and the write side (items 1 and 8)** as the only live
hypotheses for the 24% that remains, with the scale question handed to the S-split.

1. **Ground the extractor: feed it the last-k turns plus an observation date.** Source: mem0
   `main.py:922`, `prompts.py:524-535`. Our `curate_loop.py` extracts one turn in isolation, so pronouns
   and "last week" are written ungrounded — which is *the* mechanism behind the temporal failure the
   diagnostic described ("an ordering question needs three dated events"). Cost: **zero extra asks**, same
   4 asks/turn, longer prompt. Measure: temporal-reasoning accuracy on the venue (3 repeats) plus the
   right-fact-in-block probes; falsifier: temporal moves < 1 repeat-band while junk rate (measured by
   `m4_families.py` pairs) rises, meaning grounding bought verbosity rather than dates.
2. **Render validity into the block and teach event-time vs mention-time.** Source: Zep's context renderer
   (`retrieving-context.md`, `zep_longmem_eval.py:29-44`); Graphiti exposes the same as query predicates.
   Zero retrieval compute — it is a formatting change to `_render_block`, plus an `at=` filter (~20 lines
   against columns that already exist). Measure: temporal + knowledge-update slices with right-fact-in-
   block held flat (so it cannot hide behind coverage); falsifier: no movement on either slice while the
   muted control stays at `idk`.
3. **A subject axis, gathered rather than filtered.** Converged independently: Graphiti's entity rollup
   and mem0's subject index with a degree penalty (`main.py:1807-1821`,
   `boost = cos * 0.5 / (1 + 0.001(n-1)^2)`). This is NOT the shelf axis that died today — a category
   filter narrows, a subject gather *collects the multi-evidence set an ordering question needs*. Build
   it in the curation loop (not the write path), as a `subject` column plus a rollup table. Cost: one
   column, one table, no LLM. Measure: `lme_retrieval_ab.py --offline` (multi-session and temporal
   coverage are the target columns), then the venue; falsifier: coverage at k=16 does not move, which by
   the filed causal chain means accuracy cannot move.
4. **Presentation, not placement: purpose strings and an evidence-then-answer shape — the ordering half of
   this idea is already dead.** Source: Letta's core-vs-deferred tiers with a per-unit `description` saying
   what each unit is FOR, plus Cognee's deterministic answer-grounded evidence block (stopword-overlap
   filter against the generated answer, 160-char snippets, `file #seq` locators, `references.py:277-360`).
   The budget is measured dead and so is position: `tools/lme_posrank.py` finds no monotone position effect
   across 1,500 filed lines, so moving a fact earlier buys nothing. What has NOT been tested is what the
   line SAYS — 96 undifferentiated sentences versus a reader that can tell which kind of statement each one
   is. Cost: prompt shape only, no retrieval compute. Measure: venue accuracy, 3 repeats, same store, with
   coverage held flat (so it cannot hide behind evidence arrival); falsifier: a tiered/labelled block moves
   nothing either, which would say presentation cannot pay at all and the residual really is the reader
   model — a useful negative, because it ends the presentation line and points the next hour at the
   extractor instead.
5. **Empty the WILD bucket — but for the right reason.** Measured: a wild row is **4.5x more likely to be
   missed**, 18% of rows, half the misses. The falsifier written before the mechanism was checked has
   already fired analytically: `class` is not a ranking input anywhere (`serve/memory.py:910-915` uses it
   only as a `like` filter when a caller names shelves, and the blend reads rank/recency/confidence), so
   re-shelving a wild row cannot move it in the block. Keep this item for what it genuinely buys — human
   navigation in the UI, cartridge eligibility (`class_prefixes`), and filtered tool calls — and take the
   retrieval half of the 4.5x to item 3. Sources for the mechanism: Cognee's shelf telemetry +
   canonicalization (difflib cutoff 0.8, annotate-don't-reject), Graphiti's centroid subclasses. Cost:
   offline backfill, no LLM.
6. **Deterministic gates and scoping before every expensive judgment.** Source: Zep runs identity ->
   exact-name -> MinHash Jaccard >= 0.9 behind an entropy floor and pays the LLM only for the residue
   (`dedup_helpers.py:31-86`); its pair-scoping is what makes its write cost store-size-independent. Ours
   is already linear per scope but the exact-text dedup scan is O(scope) — the wall named at ~50k rows.
   Cost: index work, no LLM. Measure: writes/s into a 50k-row synthetic scope; falsifier: no slope change.
7. **Document spine: `documents` table + `doc_id` + dense `seq` + a tiling assertion.** Source: Cognee
   `incremental_chunking.py:53-66, 309-321` — and their own skeptical finding, the spine buys **citation
   and deletion, not precision** (they exclude it from fact comparison and never use it to widen a hit).
   Our `{file, n}` is already 90% of it; ~80 LOC, zero inference. Measure: a citation-resolvable rate on
   `ingest-*` answers, plus passage reassembly for the reader; falsifier: answers do not cite better, in
   which case it stays a deletion aid and gets deprioritized honestly.
8. **Junk pre-filters on the curation path.** Source: mem0's failure taxonomy (#4573: boot-file restating
   52.7%, recalled-context re-extraction — the last is exactly our self-feeding exposure). Cuts ~half of
   4 asks/turn. Measure: rows written per sweep and `m4_families.py` pair count, with right-fact-in-block
   held flat; falsifier: fewer rows but lower coverage, i.e. the filter ate evidence.
9. **Per-arm rank provenance on every hit.** Source: Letta returns `vector_rank`/`fts_rank`/
   `combined_score` per hit; we fuse and throw the evidence away. Pure instrumentation, no behaviour
   change, and it is what would have made the -0.139 diagnosis take an hour instead of a day. Measure:
   nothing — it makes the other nine measurable.
10. **Enum-constrained ids in the curation tool schema + a validate/re-patch loop.** Source: LangMem's
    `RemoveDoc.json_doc_id` validated against exactly the ids shown, with re-patch retry
    (`trustcall/_base.py:1192-1213`). Kills hallucinated supersede targets. Cost: 1-2 h. Measure: the
    existing merge audits; falsifier: no change in bad-target rate (i.e. the model was never the one
    picking wrong ids).

Deferred with a stated trigger: the query router (Cognee) and shelf densification return only at 10k-100k
rows — today's numbers are scoped to 600-2,655 and the venue store is the place to re-run them;
communities/hyper-edges/BFS expansion need a graph DB; anything needing an LLM per write or per read is
out while the single GPU serves the engine.

**Trigger fired and the first half returned (2026-10-17):** the venue store is 10,973 rows, and the router
shipped behind `memory.route_shelves` (off until the venue re-record gate) — reborn not as the recall lever
this table killed but as the contamination lever `m4_shelfcall.py` measured (leak 2–5× down, reach
identical). See docs/MEMORY_CORE.md, "The router shipped as production code". Shelf densification is still
deferred: the cosine-cut probe showed the cut only pays once something calls it, and that something now
exists.

