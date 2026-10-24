# C5 pre-registration — the bank-composition probe: do N shelf-cartridges compose into ONE standing conversation?

Written 2026-10-24 afternoon, BEFORE any C5 ask, on the owner's directive: "see if we can
actually build the MAM cartridges first" — with the reference materials fetched, not remembered.
C0 (ride-at-start is real iff the request is a token-exact continuation; PREFILL-FREE, not
TEXT-FREE), M3c (restore 81 ms for 247 MB, pin law refuses foreign files, warm 3/3 vs cold 3/3
`idk`, wrong-cartridge 0 leaks; the fresh-conversation prefill win NOT earned), and C3 (100/100
recall at the 100-fact monolith) stand as recorded. C4 (monolith capacity 100→500→1,000) is
written and unsigned; C5 is its sibling, not its sequel: **C4 asks how BIG one bank may be; C5
asks whether MANY cartridges compose at all** — the owner's MAM is many small cartridges plus
selection, and the fork ships a monolith.

## Reference basis (fetched 2026-10-24, labeled)

- **Upstream** (`ceceli33/titan-cognitive-core-v2`, AKBASCORE MAM): knowledge becomes K/V rows
  (CUT3: layer-3 residuals + layers 0-3 K/V captured, higher layers recomputed; 57,344 B/row,
  28-layer cartridge, SHA-256 manifests); READ selects by geometric addressing (PCA128 → FAISS
  IVF128 → TOP2 → PEAK50) and answers WITHOUT source text. v1.4 consolidates independently
  written cartridges APPEND-ONLY (A→AB→ABC) with "earlier rows unchanged" PASS and multi-hop
  staged answers 3/3. The author's own boundaries govern how we read it: the 96/96 panel is the
  same 48 facts twice (not blind), semantic transfer is NOT claimed, and "architectures with
  substantially different attention mechanisms" is his own open question — which is this fork's
  blocker (36 Gated DeltaNet + 12 sparse-attention layers, quantized KV, C++ engine: state-
  without-text there is research, not a result).
- **Our own scale record**: the numeric bank's answer side was measured before any bank was
  built — consolidated recall fell 12/12 → 6/12 from 5 to 20 banks on the incremental arm, and
  independent-write banks collapsed 3/20 until CacheBlend-style consolidation repaired the
  control. The hard part is the MODEL ANSWERING from composed memory, not the plumbing.

## The one question

**When N shelf-cartridges are composed into ONE standing conversation (seeded, saved, restored
at boot), (a) does the slot ride it as a stable prefix through real conversations, and (b) does
the model still answer across ALL N shelves, or does composition dilute recall the way the
numeric arm's cliff did?** If yes at N=20, the MAM bank is buildable ON THE ENGINE AS-IS (text
cartridges composed; selection by router/catalog decides which shelves ride); if it falls at
rung k, the bank stops at k−1 and full many-cartridge-per-request waits for Spike B (engine
grafting, fork work).

## Venue (fixed; the C-series shape, unchanged)

- Production engine (huihui UD-Q4_K_XL, 393k ctx), channel off, **temp 0.8** (user-measurement
  law: never 0), `max_tokens 500`, fresh conversation per ask, timings read from responses —
  never from engine logs (the venue-day scar: server stdout goes to the double-click terminal
  and is lost; C5 reads `timings.restore_ms` and `reused` from the API).
- Shelves are the LIVE PROD store's real shelving (no fixture): pick the top-N shelves by
  shelved-fact count, 20 facts per shelf fixed (equal weight per drawer, so a big USR shelf
  cannot masquerade as five shelves), call-number header per block (`[DEC/POSTG] …`), ack round
  between blocks (the seeded-conversation shape).
- Rungs, same boot, sequential restores (the C-series ran rungs this way): **R1 (control) = one
  shelf (20 facts)** → **R5 = five shelves (100)** → **R10 = 200** → **R20 = 400**.
- ≥3 repeats on the headline numbers; one-variable-per-boot law bends only for the rung ladder,
  which is a curve, not an A/B — stated here so it cannot become a confound later.

## Measurements per rung

1. **Mechanics**: `restore_ms` on boot; first conversation's turn-2+ `reused` counts > 0
   (prefix rides; C0's condition: byte-exact continuation — the ack-round shape is what makes
   it byte-stable, same law as the standing cartridge's 17-fact seed).
2. **Answer side — direct**: two probes per shelf (a fact VERBATIM from that shelf, asked in
   plain words, `idk` rule voice), scored exactly (M3c's criterion: score the final line).
   Per-shelf score recorded; the rung's number = min across shelves (the weakest drawer
   decides — a bank that loses one shelf silently is the failure mode worth catching).
3. **Answer side — aggregation**: five cross-shelf questions fixed before the run (each needs
   ≥ 2 shelves; composed from the same real facts), scored by the harness's judge. Reported;
   it is the multi-session class in miniature, and its curve is the point.
4. **Cost**: seed tokens, seed wall-time, restore bytes/ms, per-answer latency vs R1. Reported
   — the owner's budget law: right in a minute beats wrong in two seconds.
5. **Control**: one `X-Strata-Memory: off` muted ask per rung → must `idk` (nothing leaks from
   a stranger's cartridge; the M3c law re-probed at bank scale).

## Predictions (fixed before running)

- P1: mechanics pass at every rung (restore < 1 s; `reused` > 0 from turn 2).
- P2: **min-per-shelf ≥ 0.90 at R20** — text composition does NOT inherit the numeric arm's
  cliff (this is THE gate).
- P3: aggregation ≥ 3/5 at R20 and monotone non-decreasing across rungs (structure with
  headers helps a browser answer across drawers).
- P4: cost curve roughly linear in rung size (restore, seed, latency).
- P5: muted asks `idk` at every rung.

## Decision rule

- P2 holds at R20 → **bank buildable**: MEM/CAT index cartridges become its card catalog (the
  owner's catalog law), selection = which shelves the router/catalog chooses to ride; C4 then
  says how deep each drawer may get.
- P2 falls at rung k → bank capped at k−1 shelves as a PRODUCT; many-cartridges-per-request
  stays Spike B territory (engine grafting), and the browse architecture proceeds on the
  text-tool path regardless — tier-3 browsing is not gated by this probe.

## Owner signature

Pending — Clio awaits the countersign before any C5 ask is generated.
