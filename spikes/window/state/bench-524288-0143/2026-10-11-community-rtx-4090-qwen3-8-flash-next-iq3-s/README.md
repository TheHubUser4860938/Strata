# Community benchmark on NVIDIA GeForce RTX 4090

Measured on 2026-10-11 with `tools/strata_bench.py` (suite 1, 3 runs per test, medians with ranges). Speeds are the engine's own timings; first-token times are measured by the client over HTTP. Speed only: no correctness check was run.

## Hardware and software

- GPU and VRAM: NVIDIA GeForce RTX 4090, 24 GiB VRAM
- CPU: AMD Ryzen 7 9800X3D 8-Core Processor (8 cores, 16 threads); installed RAM: 62.4 GiB; storage: not measured
- OS: Linux 7.2.9-2-cachyos; GPU driver: 615.78.08; CUDA: not read
- Strata: project 0.1.43, commit 16885df6; engine 0.1.43
- Background workloads and power limits: not recorded

## Model and configuration

- Model and quantization: qwen3.8-flash-next-iq3_s
- Context: 524288 tokens; parallel slots: 1
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
| short chat | 323 (224-536) | 0 | 100 (100-100) | 9 | not measured | 100.7 (87.0-118.6) | 0.67 (0.53-97.95) |
| 4K prompt, fresh | 4032 (4032-4032) | 0 (0-0) | 24 cap | 3 | 2105.4 (1686.3-2123.1) | not measured | 1.93 (1.91-2.41) |
| 16K prompt, fresh | 16060 (16060-16060) | 0 (0-0) | 24 cap | 3 | 3979.1 (3781.8-4041.9) | not measured | 4.06 (3.99-4.27) |
| agent session, follow-up turns | 15039 (15028-15041) at the last turn | see below | up to 64 | 3 | see below | 122.0 (98.4-159.7) | 2.00 (1.85-4.05) |

Ranges are the lowest and highest of the runs; `Runs` counts requests that returned a token.

### Agent session (conversation cache)

Cache hit over the follow-up turns: 39.4 % (39.3-39.4); earlier-turn tokens read again per session: 13852 (13844-13888) (earlier turns that the cache did not hold; the 14002 (14002-14002) new tokens of the files are read once); first turn (nothing cached) first token: 1.06 s (1.04-1.32).

| Turn (run 1) | Prompt tokens | Reused | Read | Prompt tok/s | First token (s) | Decode tok/s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 778 | 0 | 778 | 595.4 | 1.32 | 95.1 |
| 1 | 4263 | 798 | 3465 | 1478.2 | 2.359 | 101.2 |
| 2 | 5498 | 771 | 4727 | 2210.1 | 2.154 | 105.8 |
| 3 | 9194 | 5491 | 3703 | 1611.2 | 2.316 | 105.5 |
| 4 | 11949 | 5491 | 6458 | 2628.5 | 2.474 | 127.9 |
| 5 | 15028 | 5491 | 9537 | 2367.7 | 4.049 | 125.1 |

Per-request data: `results.json`. Engine log lines from the start: `engine-log-excerpt.txt` (when this run started the server).

## Correctness and limitations

Not measured: this is a speed suite. The conversation uses the model's own replies, so token counts differ a little between runs. Machine details come from the server, `nvidia-smi`/`rocm-smi` and the OS; background load and power limits are not recorded. Total time of the suite: 649 s.
