# Community benchmark on NVIDIA GeForce RTX 4090

Measured on 2026-10-11 with `tools/strata_bench.py` (suite 1, 3 runs per test, medians with ranges). Speeds are the engine's own timings; first-token times are measured by the client over HTTP. Speed only: no correctness check was run.

## Hardware and software

- GPU and VRAM: NVIDIA GeForce RTX 4090, 24 GiB VRAM
- CPU: AMD Ryzen 7 9800X3D 8-Core Processor (8 cores, 16 threads); installed RAM: 62.4 GiB; storage: not measured
- OS: Linux 7.2.9-2-cachyos; GPU driver: 615.78.08; CUDA: not read
- Strata: project 0.1.43, commit 7d5124c8; engine 0.1.43
- Background workloads and power limits: not recorded

## Model and configuration

- Model and quantization: qwen3.8-flash-next-iq3_s
- Context: 393216 tokens; parallel slots: 1
- Settings: the install's own configuration, unchanged; thinking off for the suite; temperature 0

## Method

Run by `python tools/strata_bench.py` against the HTTP API of a Strata server (already running); model loading is not timed. One warm-up chat and a 1K prompt are discarded first. Every prompt starts with a unique tag, so the conversation cache cannot help the fresh-prompt tests.

- short chat: the three questions of `tools/ab_engine.py`, up to 100 new tokens, 3 runs
- 4K / 16K prompts: generated word lists sized by a calibration request, 24 new tokens, 3 fresh prompts each
- agent session: system prompt and four tools, then 5 turns that each add about 1500 words of a project file and continue from the model's own earlier answers (the way agent clients resend a conversation), 64 new tokens per turn, 3 sessions; the cache numbers come from the answer's usage (`cached_tokens`) and timings (`prompt_n`)
- concurrency (when run): N requests sent at the same moment, total tokens over the wall time of the burst, latency over all requests

## Results

| Configuration | Actual prompt tokens | Reused tokens | Generated tokens | Runs | Prompt tok/s median and range | Decode tok/s median and range | TTFT seconds median and range |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| short chat | 323 (224-536) | 0 | 100 (100-100) | 9 | not measured | 109.6 (98.8-122.2) | 0.60 (0.48-0.69) |
| 4K prompt, fresh | 4032 (4032-4032) | 0 (0-0) | 24 cap | 3 | 2231.4 (1836.4-2237.4) | not measured | 1.82 (1.82-2.21) |
| 16K prompt, fresh | 16060 (16060-16060) | 0 (0-0) | 24 cap | 3 | 4147.5 (3930.4-4151.2) | not measured | 3.90 (3.89-4.11) |
| agent session, follow-up turns | 15043 (15028-15052) at the last turn | see below | up to 64 | 3 | see below | 124.4 (99.1-181.8) | 1.98 (1.80-3.89) |

Ranges are the lowest and highest of the runs; `Runs` counts requests that returned a token.

### Agent session (conversation cache)

Cache hit over the follow-up turns: 39.4 % (39.4-39.4); earlier-turn tokens read again per session: 13855 (13826-13858) (earlier turns that the cache did not hold; the 14002 (14001-14003) new tokens of the files are read once); first turn (nothing cached) first token: 1.03 s (1.02-1.18).

| Turn (run 1) | Prompt tokens | Reused | Read | Prompt tok/s | First token (s) | Decode tok/s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 778 | 0 | 778 | 666.4 | 1.18 | 101.0 |
| 1 | 4267 | 802 | 3465 | 1513.0 | 2.307 | 107.7 |
| 2 | 5502 | 771 | 4731 | 2114.5 | 2.252 | 119.7 |
| 3 | 9198 | 5566 | 3632 | 1624.0 | 2.253 | 123.4 |
| 4 | 11977 | 5495 | 6482 | 2702.9 | 2.415 | 164.9 |
| 5 | 15043 | 5495 | 9548 | 2464.4 | 3.893 | 99.1 |

Per-request data: `results.json`. Engine log lines from the start: `engine-log-excerpt.txt` (when this run started the server).

## Correctness and limitations

Not measured: this is a speed suite. The conversation uses the model's own replies, so token counts differ a little between runs. Machine details come from the server, `nvidia-smi`/`rocm-smi` and the OS; background load and power limits are not recorded. Total time of the suite: 292 s.
