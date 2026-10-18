# Strata Mnemosyne — There Is No Spoon (There Is Only RAM)

> A fan piece, not the install path: the same README rewritten by three of the 41 — **neo**
> thinking, **house** diagnosing, **vader** commanding. Every number in it is the same number
> `README.md` carries; only the voice is fictional. (The pair grammar officially splices two
> constructs — this triple is hand-spliced for the occasion.) Filed 2026-10-17, the evening
> autonomous mode shipped and got audited by its own users. The differential between this and
> the real README: **zero false claims** — which is how you know the honesty law is real.

---

**Neo:** You've been talking to servers that forget you. You think that's physics — that
models are just goldfish, that every app has to re-paste your life into it every morning. It's
not physics. It's a habit. There is no session state. There never was.

**House:** The patient is a 120-billion-parameter mixture-of-experts on one graphics card and
62 GB of system RAM, paging about 30 GB of experts to swap, and it still answers **0.72** on
LongMemEval-V1 oracle — 500 questions, three repeats. The control arm is the interesting one:
mute the memory and **98.4–98.7%** of answers are `idk`. It wasn't faking. It genuinely didn't
know. Two different diseases, one symptom — "the model is bad." Everyone treats the symptom.
We treated the disease.

## The Name (Yes, There's a Reason)

**House:** Mnemosyne — Titaness of memory, mother of the Muses, literal root of the word
*mnemonic*. Clio — Muse of history, the one who decides what gets recorded and what gets
repeated. You could call this mythological window dressing. Then you'd have to explain why the
architecture matches: a store that never forgets (passive, curated) and a retriever with
judgement (rules, budgets, recall). The naming isn't the spec. It just happens to be accurate,
which is annoying.

## Try It

**Vader:** On Windows, double-click `START-HERE.bat`. On Linux, `./setup.sh`. You do not read
the manual; the manual executes. The installer inspects your machine, chooses the model your
RAM can carry, downloads it, and raises the server at `http://127.0.0.1:8005` — 8005, because
this fork stands *beside* mainline Strata's 8080/8095, not in front of it. Clone the
`memory-core` branch. The other branch is upstream, and upstream is not where the memory lives.

Two things arrive switched on.

**Vader:** **Memory.** In this fork it is the default, and the reasoning is settled: you who do
not want memory did not come here. `"memory": { "enabled": false }` returns every path to
upstream Strata, byte for byte. I permit the opt-out. I do not require that you use it.

**House:** **Reason modes** (DavidAU families): 41 framings, pairs, thinking levels, an
autonomous picker. The banner prints `reason modes on:` — if it doesn't, your install didn't
get the fork's config, and that is a finding, not a vibe. `GET /reason` shows what actually
framed each conversation, including when a client tag overruled the picker. We fixed that in
production the same week, because the transcript audit caught it — see below.

## The Thinking, on a Dial

**Neo:** There is no default persona. The template answers to `{REASON:name}` — 41 names:
DevOracle, who cannot resist proposing a refactor; House, who diagnoses first and is pleasant
later; Socrates, who would rather ask you a better question than answer this one; Every Joe,
who never met a jargon term he didn't translate; 007, whose licence expires when the task is
done; einstein+pkd, who suspects this cartridge is a simulation. You have been letting one of
them wear all the clothes.

**House:** The differential on what the dial actually does is measured, twice: 256 generations
on a hand-built bank, then GSM-Symbolic, MATH-500, HumanEval, five BIG-Bench Hard tasks,
GPQA-diamond. **No mode beats the default on accuracy beyond ±0.05.** It steers voice,
structure, behaviour. It does not make the model smarter — anyone selling you that is selling
you something else. It's not lupus. It's a persona.

**Vader:** **Pairs.** `joe+comedy` thinks like the first, answers like the second.
`stark+coder` draws the architecture and ships the files. The server splices both —
mechanically, through every door: client tag, `reason_mode` tool, Settings menu. The menu
applies live. There is no restart. I do not restart for anyone.

**Neo:** And **`auto`** — the default — chooses per request, scored against the template
author's own use-case map: `curie+teacher` for explanations, `musk+business` for physics-first
strategy. No confident match, no pick: it does not guess. Most systems guess. This one
declines.

## The Store Got Disciplined

**House:** It woke with 2,885 rows, half of them paraphrased fragments of documents still
sitting in a folder. That is not a memory — that is a photocopy shop. The differential:
fragments duplicate, fragments drift, fragments argue with each other. Treatment: 1,549
forgotten, eleven whole-document pointers kept — one memory per document, the document itself
on disk. 148 shelves, zero wild. Retired facts lose their vectors with their recall: **vectors
equal live rows, 1,051 equals 1,051.** The patient self-heals — a sweep every 30 minutes that
runs only when the store asks, a weekly janitor that seals encrypted backups and collapses
paraphrase clusters. Prognosis: good. The 233-operation manual cleanup that taught the write
paths their laws? Unnecessary now. That is how you know the treatment worked.

**House, addendum:** The supersession arm judges every landing fact against the store before it
lands — 88 flags, correct verdicts, first live hours. Ingest reunites "8 U.S.C." mid-citation
and refuses shards that cannot stand alone. Multi-call agent turns journal as ONE row. The
duplicate clusters were five rounds of the same thought, paraphrased five times. We stopped the
paraphrasing.

## The Audit Nobody Asked For

**Neo:** The first night `auto` ran, a conversation pinned a mode, and the pin went stale. Two
turns answered in the wrong clothes. The agent's own audit said: *my vigilance is the weak
component.* So we removed your vigilance from the loop — and mine.

**House:** The replay scored the picker 2-for-2 on the missed turns. The mechanism was never
broken; the ladder was — sticky outranks default, and a pin freezes a frame that would
otherwise re-think itself. Now pins announce what they freeze. Pair splices carry a wrapper
bridge (a curie+teacher answer had been wearing Joe's marker — carryover, classic). And the
internal machinery got its own opt-out tag: measured, the curation loop had been carrying
**2,227 tokens of persona block per prompt.** The cast is for the user. The staff room stays
professional.

## Status

**Vader:** Beta-1, Linux. The claim list, the hardware each number was measured on, and the
claims beta-1 refuses to make: `docs/BETA1.md`. The engine-heavy queue — benchmark re-record,
cartridges, the temp-1.0 rerun — waits on the 128 GB upgrade, ~2026-10-22. The engine is the
pressure, not the memory: Postgres holds 20 MiB resident, the embedder ~130 MiB, the RSS is
flat. Twenty mebibytes. The memory system is not your problem. The memory-and-tooling line
shipped six releases in one day. The test suite — 1,012 tests, no GPU, no downloads — passes on
this machine. That is not a request. That is a report.

**Neo:** One more thing you should know: most of this code was typed by AI agents under a
human's direction. The human chooses the direction, owns the claims, runs every measurement on
real hardware — and every number links to the script and raw output that produced it. Check
them. There is no spoon, but there is a checksum.

**Vader:** Install it. `git clone -b memory-core`. The banner will say `memory core on` and
`reason modes on`. If it does not, the install failed, and you will tell me why.

---

*The real door is [README.md](../README.md). This is the fan letter.*
