# M3d - the conversation-cache experiment (2026-10-13, boot 15)

**Setup:** one variable - `--conversation-cache-mib 8192` added to the engine args
(`strata-dev.json`, gitignored; `engine_args` passes config `args` through raw, no code).
Engine accepted it. First probe read `/props` and saw nulls - wrong endpoint, my error: the
card lives in `/metrics`, and it tells the whole truth: `enabled: true, budget_mib: 8192,
slots: 4`, and since-boot `parks: 37, restores: 9, requests_reused: 69 of 104,
reused_tokens: 6,481,342 of 9,000,975 prompt tokens` - 72% of prompt tokens served from
cache (that share includes same-conversation slot resume, which predates the cache; the
cache's INCREMENT is the cross-conversation returns measured below). The engine's own log
adds the rest: park, restore, evict, admit. No INFO-parse gap existed - filed against
myself, and the engine 0.1.43.1 reports the budget fine.

**Experiment: alternating conversations, timed, one script, nothing else scripted.**

| step | prompt tokens | cache_n | prompt_ms | meaning |
|---|---|---|---|---|
| A1 fresh (standing fact) | 2,811 | 0 | 1,989 | baseline prefill |
| B1 fresh (2+2) | 2,741 | 0 | 1,917 | B replaces A in the slot |
| B2 continue B | 3,126 | 2,734 | 723 | resume (worked pre-cache too) |
| **A2 return to A** | 3,301 | **2,820** | **705** | **A's parked state, not the slot's** |
| C1 muted tiny | 15 | 0 | 123 | replaces the slot again |
| **A3 return to A** | 3,312 | **2,804** | **775** | parked again - engine: "restored 2804 tokens (checkpoint) in 12.9 ms" |
| E1-E3 fresh x3 | ~2,900 | **0** | ~1,930 | cross-conversation prefix sharing: absent |

**Verdicts:**

1. **The cache's earned win: conversation parking.** A conversation displaced from the slot
   comes back from RAM in ~13 ms instead of re-prefilling ~2,800 tokens: 1,989 -> 705 ms,
   measured, repeats clean. On a box where several clients alternate (this assistant's
   session, the web app, venue runs), every return is a resume. Keep the flag on.
2. **What it does NOT do: cross-conversation prefix inheritance.** Three brand-new
   conversations after four parked ones: cache_n 0 each. The cache is keyed by
   conversation (checkpoint restore), not by shared prefix - so the cartridge's
   fresh-conversation claim stays exactly as M3c filed it: fast slot reload, pin-verified
   provenance, standing text ahead of the block; no prefill skipped for strangers.
3. **RAM admission is real and self-protecting:** a 4,797 MiB park with 4,013 available was
   refused (`skip parking ... 0 still parked`) and eviction churn followed (evictions 13 by
   run's end, ~1.2 GB parked). The big conversation (this assistant's ~160k-token session)
   does not fit the park budget at all; small ones do. `reused_kv_bytes` stays 0 while the
   "restored N tokens (checkpoint)" lines prove reuse - the counter tracks something else;
   read the lines, not the counter (same lesson as the cartridge: responses and engine
   lines over vibes).
4. **The block-after-system lever: analyzed and demoted.** It could let fresh
   conversations reuse the CART checkpoint (~840 tokens), but the block (~2,000) and client
   systems (OpenCode's ~10k) dominate the prefill anyway - ~600 ms for a real
   block-placement behavior risk, and conversations WITH a client system diverge before
   CART's end regardless. Backlog, not next.

**Next boundary unchanged:** the F venue wants its own store boot (cap 3,000 vs oracle
~6k rows). Boot 16 = dedicated venue store + F pilot-5 then full oracle, repeats >= 3.

Raw numbers: the table above is the record (single script, output in this session's log);
engine lines from `strata-dev.log`, scrub-safe (numbers and words only).
