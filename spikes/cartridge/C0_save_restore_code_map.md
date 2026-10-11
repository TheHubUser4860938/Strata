# C0 - session save/restore code map (tier-2 cartridge venue, gate C0)

Provenance: produced 2026-10-11 evening by an explore subagent (session
ses_ed3de7238ffeen8Wg07JECmW1x) over this tree (branch `memory-core`, engine 0.1.43,
CMakeLists.txt:11); every claim carries file:line as given. Purpose: answer, from code alone,
whether "install at conversation start" (restore a saved conversation as the memory prefix, then
let the question ride it) is possible, and what constraints the venue must respect. The design
lives in the personal plan (tier-2 section); this file is its evidence base.

Headline: **ride-at-start is real in the code** (restore sets `live_ok`, the next request reuses
the restored prefix and reads only its new tail) **iff the request is a token-exact continuation**
of the saved conversation's ids. The engine's reuse model is text-identity-based, so the native
port is PREFILL-FREE, not TEXT-FREE.

---

# Session save/restore map — Strata `memory-core`, engine 0.1.43 (CMakeLists.txt:11)

## 1. Slot-file contents & format

Format v1 is defined in `src/core/conversation_file.cpp` (magic `STRSESS\x01`, version, `header_bytes()` :46–49, :804–817; payload walk `put_payload` :689–708; parser `get_payload` :765–796) and documented in `docs/DETAILS.md`:1153–1160; struct of record `SavedConversation` in `include/strata/core/conversation_file.hpp`:1–7 + `include/strata/core/conversation_cache.hpp`:148–174.

Payload (`conversation_file.cpp:689–708`): 18-int geometry key (`src/core/conversation_state.cpp:14–19`: n_embd, n_layers, qsa_interval, all SSM/GDN dims, heads, head_dim, idx dims, hc, n_expert, n_ff), layer range, cvec flag, **live** checkpoint, saved checkpoints, then per-layer K/V.

- **Per-layer K/V rows** — one entry per owned QSA (full/sparse-attention) layer, then the **draft layer** last (`src/core/conversation_state.cpp:249–276`, `conversation_snapshot_sources` :278–297). For hybrid k8v4: format tag **3** (`src/core/conversation_snapshot.cpp:48–53`), INT8 K + fp16 scales + rotated-Q4 V with no V scales (`:64–71`), plus pooled indexer rows (`:47`). Draft ring under hybrid stays plain INT8 (`include/strata/core/layer.hpp:222–224`).
- **GDN/DeltaNet recurrent states** — full `gdn` rows (ssm recurrence + conv, `conversation_state.cpp:107–129`) and PLE history, in every checkpoint incl. live (`:155–171`); per-QSA-layer `idx_tail/idx_dead/idx_block_pos` (`:164–169`). Token ids + image keys (`conversation_file.cpp:682–684`).
- **Sampler state: NOT saved** — sampling/seeds/draft tuning are explicitly excluded from identity and absent from the payload (`include/strata/core/conversation_file.hpp:69–70`).
- **MTP/draft state:** only the draft layer's K/V (last kv entry, `conversation_file.hpp`:1–3); no draft RNG.
- **Checkpoints:** only the **deepest** one is written (`conversation_file.cpp:1120–1136`; engine passes it at `src/program/generate.cpp:9877–9883`). `pinned` is a run-time mark, not in the file (`conversation_cache.hpp:28–31`).
- **Pins:** header carries model fingerprint (size + first/last MiB of every loaded file, by role, `conversation_file.cpp:943–993`) and config fingerprint = engine version (`STRATA_VERSION`, `generate.cpp:7777`), backend cuda/hip (:7778–7782), `--kv`, `--max-context`, `--kv-resident`, `--mtp-window` (:7786), `STRATA_KV_ROT`, **resolved rope** (type/base/factor/freq_scale/orig_ctx/YaRN knobs, :7789–7791), cvec digest, arithmetic switches (:7793+; `conversation_file.cpp:878–900`). Read refuses any mismatch before parsing (`:1205–1210`); geometry/layer-range re-checked (`:768–774`).

## 2. Restore vs prompt-cache bookkeeping (central)

Disk RESTORE replaces the solo session wholesale: device pools written (`generate.cpp:9965`), then host bookkeeping `live`, `live_imgs`, `checks`, `cvec_cached` replaced and **`live_ok = true`** (`:9976–9981`). Nothing marks the prefix stale.

Next request: `resume` is recomputed by prefix matching (`:10251–10260`): `live_ok && starts_with(live,…)` → `resume = live.size()`, `from_live=true`. `starts_with` requires the request's token ids to begin with **exactly** the live ids and image identity to match (`:10199–10205`). Then `read_from = resume` (`:10498`), `pp_exact = true` — "a restore … the state is exactly at read_from" (`:10503`), no checkpoint re-mount needed when `from_live` (`:10460`). `RESUME <n>` printed pre-read (`:10512`), `REUSED <n>` after (`:11102`), and DONE field 8 = `resume`, field 14 = `read_n = n − resume` (`:12191–12211`).

`timings.cache_n/prompt_n` are a **server-side view of exactly that**: `_parse_done` reads `reused`/`prompt_read` from DONE (`serve/server.py:916–929`); `request_timings` sets `cache_n = reused`, and #1870 widens it from the engine's `prompt_read` (`server.py:4156–4177`, esp. :4162–4167); `/metrics` derives `prompt_n = prompt_tokens − reused` (`server.py:3338`). So a restored prefix is counted as **cache_n (reused)**, not prompt_n.

Deliberate re-prefill paths: (a) prompt doesn't prefix-match restored live **and** the single saved checkpoint → `resume = 0` → `session_zero` + read from token 0 (`:10451–10459`); this is exactly the observed "restore into an existing conversation re-reads everything" — restoring B overwrites A's live/checks (Q4), so A's next prompt matches neither; also any template/re-render divergence, or an edit past the checkpoint, since only the deepest checkpoint survives (`conversation_file.cpp:1120–1124`; `docs/DETAILS.md`:1140–1141 confirms reuse + "an edit further back reads more again"). (b) cvec mismatch clears live/checks (`:10426–10430`). (c) `nan_flush` (#879) drops everything, resume=0 (`:10227–10250`). (d) test-only `STRATA_CKPT_REREAD` re-reads instead of mounting (`:10464–10480`).

## 3. `valid_extent()` / #1444

`valid_extent` (`src/core/conversation_snapshot.cpp:20–27`, SYCL twin `sycl/src/core/conversation_snapshot.cpp:21`) fails "conversation snapshot: invalid K/V extent" when `upto < 0`, `upto > st.max_cells`, or `upto` can't take page rounding. `upto` = live token count; for the **draft layer** `st` = `mtp.kv_state()`. The disk SAVE/RESTORE path passes `mtp.kv_state()` **unconditionally** (`generate.cpp:9889, 9934–9936, 9952, 9965`; same in sycl :9234–9313). With `--mtp` off, `MtpDrafter` is never loaded (`:4453`), so `st_` is a default `QsaState` with `max_cells = 0` (`include/strata/core/layer.hpp:231`) → any save of a non-empty conversation fails with exactly that message. The RAM-park path handles this with `nullptr` ("no --mtp: no draft K/V (snapshots accept null)", `generate.cpp:7836`), but **no such gate exists in the disk path — the no-MTP cause of #1444 is NOT fixed in this tree** (no #1492 reference anywhere; `docs/MEMORY_CORE.md`:74,161 still lists it as unreproduced-but-open). Related refusals: hybrid-as-ring (`conversation_snapshot.cpp:31–33`) and `valid()`'s "invalid K/V extent or missing authoritative host pool" (:88–95: cells>n_pages, kv_mode==0 with n_slots<n_pages, pooled_rows>idx_pooled_rows, streamed mode without host pool).

## 4. Single-slot concurrency

SAVE/RESTORE are stdin commands handled **between requests only** (`generate.cpp:9764–9774`); the server holds the service FIFO so they queue behind the running request (`serve/server.py:2900–2915`; `docs/DETAILS.md`:1138–1139). Refused under `--layer-split`/`--peer-device`/`--batch`, and need `--prompt-cache > 0` (`:9806–9809`; server 501 for parallel, `server.py:2878–2881`).

RESTORE with conversation A resident: A's device state is overwritten in place and its host bookkeeping replaced (`:9965, 9976–9981`); **A is not parked** — `park_current` runs only at the start of the next GEN (`:10321`; `docs/DETAILS.md`:1191 "A restore does not park the outgoing session"). A survives only if it was already parked. Parking (`--conversation-cache-mib`, cache built `:7734–7736`): parked entries **survive** a restore (nothing in the RESTORE branch touches `entries_`); the restore's RAM preflight asks for parse-peak above the `--conversation-cache-min-free-mib` floor and simply refuses (503) rather than evicting (`:9922–9933`). The restored K/V is *not* retained for cheap re-park (no `conversations.retain()` here, unlike RAM restore `:10415–10419`), and `conversations.take_reuse()` drops the outgoing session's retained K/V (`:9961`). The **restored** conversation B displaces parked ones only later, when the next request parks it via `make_room`/`evict_oldest` (oldest unpinned first, `conversation_cache.hpp:266–290`). Exception: a restore after non-finite logits clears the whole cache (`:9982–9988`, #879).

## 5. `--session-save-reclaim`

Parsed `:2010`; used **only in SAVE**, before checkpoint selection (`:9853–9866` → `include/strata/core/session_save_reclaim.hpp:17–53`): when the RAM preflight would fail, it frees **reconstructible host caches** in order — retained K/V (`:27–31`), oldest unpinned parked conversations (`:33–40`), then unpinned checkpoints other than the one the file will select (`:41–51`) — re-probing `/proc/meminfo`-style availability after each eviction, never lowering the floor, never touching pins or the live device state. It changes **no file bytes** (the file always held only the deepest checkpoint) and never affects RESTORE (`docs/DETAILS.md`:1108–1126; MEMORY_CORE.md:55 dates it to 0.1.42, present in this 0.1.43 tree). Server side it enables the bounded retry: `server.py:2911–2913` routes save through `serve/session_save_retry.py` — ≤3 retries within 30 s, only for an *unpublished* `memory` refusal whose reported need can be re-measured, same live engine process (`session_save_retry.py:12–14, 35–37, 49–84`).

## 6. Verdict

**Yes — in the same slot, a fresh request rides the restored prefix.** Restore sets `live_ok=true` (`generate.cpp:9981`); the next request matches live (`:10253–10254`), reads only `[resume, n)` (`:10498, :10503`), and reports `reused=resume`, `prompt_read=n−resume` (`:12191–12211`), which the server surfaces as `cache_n`/`prompt_n` (`server.py:4162–4167`). The ride requires: exact token+image prefix identity (`:10199–10205`), prompt strictly longer than live (`:10201`), same cvec state (`:10253, :10426`), `--prompt-cache > 0` (`:9809`), no pending nan-flush (`:10227`), and elastic-K/V cells mappable to the saved length (`:9957`). What breaks it: any prompt divergence (re-rendered template, stop-token handling, edited history) → fallback to the single saved checkpoint or `resume=0` full re-read (`:10451–10459`); restoring B then querying A is that case by construction (A unparked, `:1191` doc). Byte-exact replay across the restart isn't promised either (expert residency rounding, `docs/DETAILS.md`:1199–1204).

## Server-layer guarantees (one line each)

- `serve/test_session_save_retry.py` — pins retry semantics: no wait/poll on success, and retries only unpublished typed-`memory` refusals on the same live process with the floor intact, ≤3 within a 30 s clock budget (e.g. :53–64).
- `serve/test_security.py:278–295` — `/slots/0` requires `Content-Type: application/json` and no/own-page `Origin` (cross-site Origin → 403 even with an API key; disabled path → 501); filename validation is `slot_filename_problem` (`server.py:2755–2777`: plain name, no separators/drives/streams/device names/dots/control chars), engine errors map by kind to 400/507/503/500 (`SessionRefused.STATUS`, `server.py:326–333`).

## Follow-ups this map raises (not answered by C0)

- #1444 no-MTP disk-save bug is unfixed in this tree — worth an upstream issue with the
  `generate.cpp:7836` nullptr precedent as the suggested fix (the RAM path already handles it).
- C1 must verify the ride **through the server** (`/slots/0` restore + same-conversation
  continuation reports `cache_n > 0` in `timings`), not just engine-direct: the server's own
  conversation bookkeeping vs the replaced engine live is untested territory.
- Live-boot precondition audit 2026-10-11 (this session): `--mtp` present in dev cmdline (dodges
  #1444); `--prompt-cache` not set explicitly, default is 6 (`generate.cpp:615`) > 0 — satisfied.
