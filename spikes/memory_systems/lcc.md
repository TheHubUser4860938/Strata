# LCC — Library of Congress Classification (studied 2026-10-15, from the primary sources)

Our taxonomy is named after this system, so the bar for "do we actually understand it" is high. Read from
the Library's own pages and from the schedule PDFs themselves, not from summaries:

- <https://www.loc.gov/catdir/cpso/lcc.html> — the Library's own description of the notation.
- <https://www.loc.gov/catdir/cpso/lcco/> — the 21-class outline (per-class PDF/WordPerfect/Word).
- <https://www.loc.gov/aba/publications/FreeLCC/freelcc.html> — the 44 print-ready schedule volumes,
  each as **Preface / Outline / Text** (some with Cutter tables). "Data for these files was selected in
  May 2024"; earlier editions exist "but should not be used for cataloging".
- <https://en.wikipedia.org/wiki/Library_of_Congress_Classification> — history, the 21 classes, the
  criticism, and the three evaluation axes the field uses.
- <https://lis.academy/information-processing-retrieval/exploring-library-congress-classification-system/> —
  the practical/Indian-library reading of the same material, and the digital-history thread.
- Downloaded and read directly (`/tmp/opencode/lcc/`): `lcco_{q,t,z,b}.pdf` (the class outlines) and the
  FreeLCC **Text** volumes `LCC_Q2024TEXT.pdf` (44,645 lines of text) and `LCC_Z2024TEXT.pdf` (36,108),
  plus `LCC_Q_PRF.pdf`. Every caption number quoted below was read out of those two files tonight.

## 1. What the notation actually is

A call number is **not an ID, it is an address written in five positions**, each doing a different job:

```
   G   B    2403.2   .B44      2010     ("Copy 1.")
   │   │    │        │         │        └ copy/location modifier, added by the holding library
   │   │    │        │         └ publication year — separates editions of the same work
   │   │    │        └ CUTTER number: a within-topic discriminator (author, place, form, or
   │   │                 caption-initial), from published tables; also orders neighbours
   │   │    └ caption number: 1-4 digit whole number, extendable by decimals, or a SPAN (2403.2-2405)
   │   └ subclass: 2-3 letters (GB = physical geography)
   └ class: one letter (G = geography, anthropology, recreation)
```

Verified in the schedule text tonight, at the level of individual captions:

```
   QA75.5-76.95     Electronic computers. Computer science          (class Q, Science)
   QA76.75-76.765      Computer software
   QA76.758              Software engineering
   QA76.9.A-Z        Other topics, A-Z
   QA76.9.A23          Abstract data types
   QA76.9.A25          Access control. Computer security
   QA76.9.A43          Algorithms
   QA76.9.A73          Architecture, Computer
   QA325             Self-organizing systems. Conscious automata
   QA325.5             Machine learning
   QA325.6             Reinforcement learning
   QA325.73            Deep learning
   QA327             Perceptron theory     Cf. TK7882.P3 Pattern recognition systems
   TK7885-7895     Computer engineering. Computer hardware          (class T, Technology)
   Z699-699.5      Machine methods of information and retrieval.
                        Mechanized bibliographic control            (class Z, library/information science)
   Z699+              Machine-readable bibliographic data. Information storage and retrieval systems
   ZA4450-4460     Databases
   BF309-499       Consciousness. Cognition                        (class B, philosophy/psychology)
```

Three structural facts that summaries usually miss, and that matter more to us than the class list:

1. **Hierarchy lives in the indentation of the outline, not in the number.** The Library says so in as many
   words: "Relationships among topics in LCC are shown *not* by the numbers that are assigned to them, but
   by indenting subtopics under the larger topics that they are a part of, much like an outline. In this
   respect, it is different from more strictly hierarchical classification systems, such as the Dewey
   Decimal Classification." A number is a *label for a caption*, and the caption's parent is a property of
   the schedule, not of the digits. (This is the single most common thing people get wrong when they say
   "we use LCC" and mean "we use a prefix tree".)
2. **A caption is a page, not a string.** Every real caption carries scope notes and edges:
   `Including blogs, online bulletin boards, podcasts, RSS feeds, wikis, etc.` · `Class here works on
   networks not limited by topic` · `For works on structured databases see QA76.9.D26` ·
   `Cf. Z699+ …`. The tree gives the shelving order; **the `Cf.`/`For … see …` graph is what makes the
   material findable**, because a subject can only be shelved in one place.
3. **Numbers are sparse on purpose, and nothing is ever renumbered.** The Q preface spells out the
   devices: `(667.6-.63)` are "formerly valid numbers that are now obsolete" — kept, with a `see`
   reference; `<…>` angle brackets are "optional numbers that have never been used at the Library of
   Congress but are provided for other libraries that wish to use them"; and `76.9.A-Z` is an
   **alphabetical Cutter expansion inside a fixed range**, so a brand-new topic (`Abstract data types` →
   `.A23`, `Algorithms` → `.A43`) is inserted between existing ones without touching a single other
   number. The field's own evaluation axes are **expressiveness, hospitality and brevity** — LCC scores
   low on the first and high on the second precisely because of these three devices (five letters —
   I, O, W, X, Y — are still unassigned).

Governance, because it is the part a home-built taxonomy always skips: new numbers and captions come from
**proposals** by cataloging staff and cooperating institutions, approved at an editorial meeting of the
Policy, Training & Cooperative Programs Division, then created as records in a master classification
**database whose index terms are maintained by assistant editors**. Annual editions since 2015; print
editions ended in 2013; the schedules are also shipped as MARC 21 records. Each schedule was written by a
different subject specialist, which is why the literature describes LCC as "a coordinated series of
special classes rather than a unified philosophical structure" — and why it is **enumerative**: captions
exist because material exists in the collection (**literary warrant**), not because a theory of knowledge
has a slot there.

Known failure modes, stated in the sources and worth quoting when someone proposes a discipline-shaped
taxonomy for a personal store: it cannot serve interdisciplinary material well (one book, one shelf);
it carries an American/European/Christian bias in the early schedules (D, E/F, B) and an "othering"
problem for marginalized groups; and it is less expressive than DDC. The profession's answer is not a
better tree — it is a **second, separate system**: Library of Congress Subject Headings (LCSH), a
controlled many-to-many label set, cross-referenced to the schedules. Classification = one address.
Headings = many access points. They are deliberately not the same table.

## 2. What this says about our store, next to tonight's numbers

Measured today on the accumulated store (2,796 rows; `tools/m4_shelfcall.py`, `tools/m4_shelfcut.py`,
`docs/MEMORY_CORE.md` "Shelf isolation"): scoped purity **1.000** at every shelf and budget; unscoped
contamination rising with the block (TECH 7.2→17.3%, EVAL 41.4→79.8%, MEM 69.1→84.2%); 20.3% of rows
unclassified; five shelves wider than the block = 74% of the store; a cosine re-cut of `POL/ARSA` raised
reach 0.947→1.000 while a token-named cut dropped `TECH` to 0.650 and left 46% of the shelf in a `_rest`
pile. Read against LCC, six things follow:

1. **We have one column doing the job of two systems.** LCC keeps *address* (call number) and *access*
   (index terms, `Cf.`/`see` edges, LCSH headings) in separate places. Our `class` column is asked to be
   both, which is exactly the shape of the measured failure: a fact with no address (wild, 20.3%) is also
   a fact with no access point, and a fact with an address is invisible to a conversation that doesn't
   name it (hence 41-84% contamination in the unscoped path). The LCC-faithful move is a many-to-many
   **subjects table** (term → row id, controlled vocabulary, the thing the extract step already half-
   produces) while `class` stays the single address. This is testable offline against tonight's probes:
   does heading-based routing beat single-shelf routing on pick accuracy and leak?
2. **We renumber; LCC retires.** `m4_shelfcut.py` rewrites `class` for 575-863 rows per cut. LCC would
   keep `(POL/ARSA)` as an obsolete caption with a `see` reference. For us renumbering is not cosmetic:
   the tier-2 cartridge is valid **only for its exact bytes** (measured 2026-10-12), so re-shelving rows
   silently invalidates a pinned cartridge, and the block pin key contains the shelf string. Sparse
   ranges + retire-with-see is the cheap fix, and it is what `76.9.A-Z` exists to make unnecessary.
3. **Our keyword map had no literary warrant; the cosine cut does.** The map was written before the
   collection existed and never re-derived — which is why it produced five shelves wider than the block
   and a 20% wild bucket. LCC's rule is the opposite: a caption is created only when material shows up,
   by proposal, with an approved scope note. Tonight's cosine cut is literary warrant rediscovered
   empirically (cut where the rows actually are, cap at what the block can hold).
4. **A caption should carry scope notes, and ours carry none.** `Including …`, `Class here …`,
   `For X see Y`, `Cf. …` are what let a classifier *defend* an assignment and let a reader audit one.
   Our shelf marks are bare token lists in `CLASS_MAP`, so an extractor's shelf choice is unfalsifiable.
   A schedule-table-shaped `class` definition (caption + inclusion list + exclusion/see-also) is a real,
   small artifact — and it is what would let the curation loop report *why* a row landed where it did.
5. **The sub-shelf formatting question has an LCC answer: facets, not names.** The recurring cuts inside
   a real schedule are standard, reused devices — `A-Z by name`, `by region or country, A-Z` (subarranged
   by **Table Z15**), `.A2 / .A3-Z` = "early through 1800 / 1801-", form divisions for periodicals,
   dictionaries, congresses. Our shelves mix two kinds of mark in one column: **discipline addresses**
   (TECH, AI, HW, MEM ≈ QA76.x, QA325.x, TK7885-7895, Z699+) and **facets of the person** (USR, DEC,
   OPS, STR, EVAL). LCC never puts a facet in the class position — it puts it in the Cutter or in a
   standard subdivision. That is the concrete "better way to format the shelves and sub-shelves": keep
   `class` as the discipline address, and move who/when/why-kind out into their own positions.
6. **We cannot express a span, and adjacency is a primitive we are missing.** `Z699+`, `E201-298`,
   `76.9.A-Z` — a call number is *sortable*, so "the neighbours of this fact" is one range query. Our
   `class LIKE 'POL%'` can only express a subtree. The thing that makes a library usable is that related
   things sit next to each other *in a defined order*; nothing in our store has an order.

## 3. The books-and-chapters idea, in LCC terms

The user's Reddit recollection (a home-built memory that organizes everything into **books and
chapters**) is not in memory — searched, nothing found (see journal). But the design it describes is the
one thing LCC has that our store does not, and it is already on the plan as backlog item 7 (document
spine: `doc_id` + `seq` + tiling assertion), because today's ingest "lacks document-level objects,
offsets, or order, preventing expansion of search hits back into their original passage context".

Translated: a **book** is the item with the call number, a **chapter** is the position inside it, and the
Cutter + part extension (`.B44 2010`, `76.9.A43`) is precisely a chapter address. A flat embedding index
answers "which sentence is near this question"; a book/chapter spine additionally answers "what is
*next*, what is the *argument around* this sentence, what else is in *this* chapter" — adjacency and
containment, which is exactly the primitive LCC gets from sortable numbers and we cannot get from a
`LIKE` prefix. That is also the honest reason the idea is worth stealing: tonight's measurements say
retrieval is near its ceiling on this corpus (0.936 block coverage; 244 of 346 wrong answers had every
gold session present — the failure was reasoning over the block, not finding it). More retrieval cannot
fix that; **more context per hit** might, and a spine is what makes "give me the chapter, not the
sentence" cheap.

## 4. Falsifiers, in order of cost (all port-free except where noted)

1. **Subjects vs single address** (offline, ~minutes): build the many-to-many term table from the store's
   own rare tokens, route with it, and compare against tonight's routed arm (leak 1.4-36.8%, reach
   unchanged). Fails if heading routing does not lower leak or costs reach.
2. **Adjacency arm** (offline): fill the block's remaining slots by call-number *neighbours* of the top
   hits instead of by next-best score. Fails if rendered-block gold coverage does not improve — coverage
   is the metric that predicted accuracy (+0.048 → +0.040), so this is measured against the right thing.
3. **Retire vs renumber** (offline, trivial): count cartridge invalidations caused by a re-shelve. We
   already know the cartridge is byte-exact, so the expected answer is "every re-shelve costs a re-seed";
   the number is for the docs, not for the decision.
4. **Caption-with-scope-notes arm** (needs a boot, venue): give the extractor real captions with
   `Including`/`For … see …` notes instead of a token map, and measure shelf-choice agreement and wild
   share (20.3% today). Fails if wild share and pick accuracy do not move.

## 5. Where the analogy breaks, said plainly

LCC classifies *published works chosen by someone else* for *one physical shelf* in a building where the
reader can walk the stacks. Our rows are extracted sentences, they are re-derived continuously, the
reader is a model with an attention window rather than a person with a map of the stacks, and the block
holds 96 notes — so a caption finer than ~90 rows buys nothing on the shelf and only helps if something
*calls* it (measured today: relabeling changed unscoped contamination by exactly 0.0 points). LCC's
machinery is worth copying where it removes work or makes an address auditable — sparse ranges, retire-
don't-renumber, scope notes, a separate access layer, sortable neighbours — and not worth copying where
it is a consequence of paper: one-place-only shelving, and the discipline-shaped class list itself.
