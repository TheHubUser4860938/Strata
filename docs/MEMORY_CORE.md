# Memory Core — server-side, harness-independent memory (this fork's track)

**Status: Stage 0 — dev install scaffolded; engine and server code untouched so far.
Phase 1 (memory service) is next. Last updated: 2026-10-10.**

This fork explores built-in long-term memory for Strata: recall that lives at the inference server, shared by
every client (OpenCode, Odysseus AI, the web app), with **no harness ever resending memory text into the
context window**. Following upstream rules, everything stays **opt-in**: the default serving path stays
byte-identical to the release it forked from (v0.1.42, `61b3fb5`).

- **Phase 1 — memory service (building):** a curated store (SQLite, FTS5 + a small local embedder) and
  automatic recall injected as a stable hidden block ahead of the conversation. Persistence rides on
  Strata's existing machinery: system-prompt-root checkpoints, pinned message boundaries, parked
  conversations, and `--slot-save-path` session files. Harness cost of memory: zero tokens resent, near-zero
  prefill re-read.
- **Phase 2 — numerical cartridges (research):** porting the *AKBASCORE MAM / NIRVANA cognitive cartridge*
  idea (Akbaş): facts read once become model-produced K/V + hidden-state representations the frozen model
  reads back without the source text ever returning. On this model that is **open research, not an established
  result** — see the architecture caveat below. Positive *and* negative results get recorded here.

## The dev install (how to work on this fork without risking the running model)

The machine runs the production engine at `:8004` from a separate, unmodified `~/Strata` install. RAM is the
hard constraint, measured on 2026-10-10: one engine sits at **50.7 GiB resident** of 62 GiB total — with
production up only ~5 GiB is free — so **exactly one engine runs at a time**:

```
./dev-up.sh     # stops production, starts this tree's engine on :8005 (1-3 min weight load, page-cache warm)
./dev-down.sh   # stops dev, brings production :8004 back (detached, no browser; checkpoints are gone -
                # first request re-reads prompt history, ~1-2 min on long sessions)
```

- Model files are **not duplicated**: `Strata-data -> ~/Strata-data` (a symlink; git-ignored) shares
  `models/`, `mtp/`, `packs/` with production. Writes keep hygiene rules: dev logs into this tree,
  session files into `Strata-data/sessions-dev/`, rebuilt packs into new `packs/<name>-dev/` dirs — never
  overwrite production artifacts in place.
- `engine/` was copied from the production build: `engine/BUILD.json` records `source=local, arch=89,
  src=1e0c97588848ea0f` at v0.1.42 — this checkout's source recompiled for the RTX 4090. `build/` is not
  copied (CMake caches are path-baked); first C++ work regenerates it (nvcc wants production down: ~1-2 GiB
  per parallel job against ~5 GiB spare).
- Engine config: `strata-dev.json` (git-ignored per upstream convention), port **8005**, same tuning args as
  production plus the config key `"slot_save_path": "Strata-data/sessions-dev"`. Note: `--slot-save-path` is a
  **server** flag / config key — the *engine* binary rejects it (`unknown argument`, found on the first dev
  boot 2026-10-10). A template ships as `strata-dev.json.example` — set your own paths and the `$HOME/Strata`
  fallback for `PRODROOT` if your production install lives elsewhere.
- Session-file sizing (docs/DETAILS.md, measured upstream): ~**1.2 GB per 63K-token conversation** (~19 KB/token
  for k8v4 KV + draft state), files mode 0600, **nothing deletes them automatically** — the sessions-dev dir
  needs periodic cleanup. This number sizes the Phase 1/2 memory-prefix persistence budget.
- **Testing protocol on this machine (one engine at a time, 62 GB RAM):** testing the dev build pauses this
  agent (the assistant itself is served by whichever engine is up on :8004). The swap is coordinated with the
  human: agent says "ready to test dev", human closes the main build and starts dev (or the agent runs
  `./dev-up.sh --force` once agreed), tests run against :8005, then `./dev-down.sh` hands :8004 back.
  `dev-up.sh` refuses to stop a live production without `--force`. Optionally, adding a provider for
  `http://127.0.0.1:8005/v1` in the harness lets the agent keep talking *on the dev model* while dev is up.
- Running model on this machine: SC117 `Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF` IQ3_S (saved under the
  official filenames — check identity with `tools/strata_inspect.py`, not names), with the
  `sleepyeldrazi` **Q8_0 PLE** n-gram shard.

## Stage 0 smoke (measured)

Baseline from production `:8004`, greedy, thinking off, `strata_checkpoint: false`:

| check | :8004 (production) | :8005 (dev) | verdict |
| --- | --- | --- | --- |
| `/health` | ok (max_context 524288, model qwen3.8-flash-next-iq3_s) | pending | pending |
| greedy echo "STRATA-SMOKE-7790-OK" | `STRATA-SMOKE-7790-OK` | pending | pending |
| load time to health | — | pending | pending |

## Phase 1 design (summary)

Store: `Strata-data/memory/` (SQLite WAL; records with text, provenance, confidence, decay; namespaces:
one `user` profile shared across harnesses + per-project overlays). Retrieval: FTS5 + local ONNX MiniLM-class
embedder, hybrid-ranked. Injection: one versioned, byte-stable hidden block per conversation, updated only
at idle so Strata's prefix checkpoints stay hot; rendered above the chat template, template untouched.
Writes: every turn journaled raw; idle-time distillation through Strata itself proposes durable facts,
deduped against the store, provenance kept. Control: `/memory` list/search/delete + per-conversation mute.

Gate 1 (after the eval harness): cold-recall QA accuracy ≥ baseline + 25 points with the block ≤ ~1.5% of
the context window — measured on a personal ~50-question cross-harness set; numbers land here.

## Phase 2 caveats the spikes must answer (why this is research)

- Qwen3.8-Flash-Next is 48 layers: **36 × Gated DeltaNet (fixed-size recurrent state, no per-token KV)** +
  **12 × Qwen Sparse Attention (real KV, 2 KV heads)**. MAM's append-only, bitwise-stable, RoPE-re-phase
  arithmetic lives in the 12 KV layers only; GDN state is order-entangled.
- KV is quantized (`--kv k8v4`) and RoPE is YaRN ×2 at 512K — re-phasing stored keys must survive both.
- Zero-embedding consolidation forwards have no token IDs, but PLE is a per-ID n-gram lookup (here the Q8
  table) — synthetic memory rows need a defined PLE story.
- MAM's own published envelope: dense, BF16, full-attention 7B models; scaling sealed at 22/80 correct on 80
  cartridges. Gate 3: grafted-cartridge recall ≥ 0.9 × text-injection on the QA set, or cartridges stay
  research notes and the text-memory layer ships as the product.
- Session save/restore on this config is itself unproven: upstream issue #1444 shows save failures
  (`invalid K/V extent`) on some setups; Spike A tests our exact stack (k8v4 + MTP + YaRN + Q8 PLE).

## Licensing and attribution

- Strata (upstream) is MIT © Niko1221 and contributors; this fork's independent code keeps MIT.
- MAM / NIRVANA material: "AKBASCORE RESEARCH SOFTWARE LICENSE" © 2026 Mustafa Akbaş — ideas, algorithms and
  independently developed implementations are **not** covered (their license §6); research, personal and
  sub-$50M commercial use of the material are permitted; any copied or substantially derived code stays in
  clearly marked files with attribution. Prior MIT-era AkbasCore material remains MIT with its attribution
  notice retained.
- Technical disclosures cited: doi 10.5281/zenodo.23127434, 10.5281/zenodo.23143605, 10.5281/zenodo.23146475.
  Closest published systems work: CacheBlend (arXiv 2405.16444), Prompt Cache (arXiv 2311.04934).
