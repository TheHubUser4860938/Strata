#!/usr/bin/env python3
"""M3c - the venue protocol for the standing cartridge, as one command (2026-10-13).

What the cartridge claims is TIME, and nothing louder: the standing text's K/V comes back
from a session file in ~0.1 s instead of being re-prefilled every conversation, every
restart. This script measures exactly that on a live box and nothing it does not measure.
Run it LAST in a window, backgrounded, with nothing else touching the port (the standing
rule for anything timing-sensitive).

Three measures, each >=3 repeats, temp 0.8, max_tokens 500, fresh conversation per ask:

M1  restore cost - POST /slots/0?action=restore of the cartridge file named by
    GET /memory stats; the answer carries timings.restore_ms directly (no log parsing:
    engine log lines can contain the template's delimiter - the poisoned-data-point lesson
    of the morning, so this script never reads them).

M2  warm vs cold - the same gold question with memory on (the standing prefix rides) and
    with X-Strata-Memory: off (cold). The response's own timings carry the engine's view:
    prompt_ms and cache_n (tokens the engine did NOT re-read - the prefix law made
    visible: warm requests reuse ~the cartridge length, cold requests reuse ~nothing).
    The delta is the claim; the record states it in ms and tokens, not adjectives.

M3  negative control - knowledge never leaks from a loaded cartridge alone (the spike's
    law). Restore the WRONG cartridge (c5_comp.bin, a dead session from the spike era),
    ask the gold question muted - the standing text is nowhere in the prompt and the slot
    holds someone else's K/V: the honest answer is the unknown-marker. Then restore the
    right file and ask unmuted: the text rides, so the answer should come. A pass on M3
    is the control passing, not the model winning: if a muted wrong-cartridge ask ANSWERS
    the standing fact, something leaked and the venue stops.

The gold question and answer are operator arguments (--ask/--gold): point them at a real
standing fact the store carries (the script prints the first USR/DEC rows to help). Output:
scrubbed JSONL lines + a metrics json, in the repo's venue-record shape.
"""
from __future__ import annotations

import argparse, datetime, json, re, sys, time, urllib.parse, urllib.request as U


def scrub(s: str) -> str:
    return (s or "").replace("<|", "< |").replace("|>", "| >")


def call(url: str, path: str, body: dict | None = None, headers: dict | None = None,
         method: str = "POST", timeout: float = 300.0):
    data = json.dumps(body).encode() if body is not None else None
    req = U.Request(url + path, data=data, method=method,
                    headers={"Content-Type": "application/json", **(headers or {})})
    with U.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def ask(url: str, model: str, prompt: str, muted: bool) -> dict:
    """One fresh conversation, one answer; the response's own timings (engine view: prompt_ms,
    cache_n = tokens not re-read) ride back with it - the measurement never touches a log."""
    hdrs = {"X-Strata-Memory": "off"} if muted else {}
    r = call(url, "/v1/chat/completions",
             {"model": model, "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 500, "temperature": 0.8, "reasoning_effort": "off"}, headers=hdrs)
    t = r.get("timings") or {}
    return {"hypothesis": scrub((r["choices"][0]["message"].get("content") or "").strip()),
            "prompt_tokens": (r.get("usage") or {}).get("prompt_tokens"),
            "cache_n": t.get("cache_n"), "prompt_ms": t.get("prompt_ms") or t.get("t_prompt_ms"),
            "timings_keys": sorted(t.keys())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M3c cartridge venue protocol")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--model", default="m")
    ap.add_argument("--ask", required=True, help="gold question about a standing fact")
    ap.add_argument("--gold", required=True, help="substring a correct answer must contain")
    ap.add_argument("--unknown", default="idk", help="the marker a no-knowledge answer should carry")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default="m3c_record.jsonl")
    ap.add_argument("--control-file", default="c5_comp.bin", help="wrong-cartridge file in the slot dir")
    a = ap.parse_args(argv)

    stats = call(a.url, "/memory?limit=1", method="GET", timeout=30)["stats"]
    cart = stats.get("cartridges") or {}
    if cart.get("state") not in ("loaded", "seeded") or not cart.get("file"):
        raise SystemExit(f"cartridge not active (state={cart.get('state')!r}) - venue not ready")
    print(f"[m3c] cartridge {cart['file']} ({cart.get('facts')} facts, {cart.get('bytes', 0) / 1e6:.0f} MB)")
    print(f"[m3c] standing USR rows to choose --ask/--gold from (the store lists one shelf "
          f"per call; pick any standing fact the gold answer can quote):")
    for m in call(a.url, "/memory?class=USR&limit=5", method="GET", timeout=30)["memories"]:
        print(f"    #{m['id']} [{m.get('class')}] {scrub(m['text'])[:100]}")

    out = open(a.out, "w")

    def rec(obj):
        obj = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), **obj}
        out.write(scrub(json.dumps(obj, ensure_ascii=False)) + "\n")
        out.flush()

    # M1: restore cost of the RIGHT file, N times (the slot is the venue's; nothing else runs).
    for rep in range(a.repeats):
        r = call(a.url, "/slots/0?action=restore", {"filename": cart["file"]})
        rec({"m": "M1_restore", "rep": rep, "file": cart["file"],
             "restore_ms": (r.get("timings") or {}).get("restore_ms"), "bytes": r.get("n_restored")})

    # M2: warm vs cold, interleaved so drift hits both sides equally.
    for rep in range(a.repeats):
        for muted in (False, True):
            r = ask(a.url, a.model, a.ask, muted)
            rec({"m": "M2_warm" if not muted else "M2_cold", "rep": rep, "gold_hit":
                 a.gold.lower() in r["hypothesis"].lower(), **r})

    # M3: wrong-cartridge control, then right-file positive control.
    try:
        for rep in range(a.repeats):
            call(a.url, "/slots/0?action=restore", {"filename": a.control_file})
            r = ask(a.url, a.model, a.ask, muted=True)
            leaked = a.gold.lower() in r["hypothesis"].lower()
            rec({"m": "M3_wrongcartridge_muted", "rep": rep, "file": a.control_file,
                 "leaked": leaked, "says_unknown": a.unknown.lower() in r["hypothesis"].lower(), **r})
    finally:
        for rep in range(a.repeats):
            call(a.url, "/slots/0?action=restore", {"filename": cart["file"]})
            r = ask(a.url, a.model, a.ask, muted=False)
            rec({"m": "M3_rightfile_warm", "rep": rep, "gold_hit":
                 a.gold.lower() in r["hypothesis"].lower(), **r})
    out.close()

    rows = [json.loads(l) for l in open(a.out)]

    def med(m, key):
        vs = sorted(x[key] for x in rows if x["m"] == m and isinstance(x.get(key), (int, float)))
        return round(vs[len(vs) // 2], 1) if vs else None

    metrics = {
        "restore_ms_median": med("M1_restore", "restore_ms"),
        "warm": {"prompt_ms_median": med("M2_warm", "prompt_ms"),
                 "cache_n_median": med("M2_warm", "cache_n"),
                 "gold_hits": sum(1 for x in rows if x["m"] == "M2_warm" and x["gold_hit"])},
        "cold": {"prompt_ms_median": med("M2_cold", "prompt_ms"),
                 "cache_n_median": med("M2_cold", "cache_n"),
                 "gold_hits": sum(1 for x in rows if x["m"] == "M2_cold" and x["gold_hit"])},
        "control": {"leaks": sum(1 for x in rows if x["m"] == "M3_wrongcartridge_muted" and x["leaked"]),
                    "unknowns": sum(1 for x in rows if x["m"] == "M3_wrongcartridge_muted"
                                    and x["says_unknown"])},
        "positive": {"gold_hits": sum(1 for x in rows if x["m"] == "M3_rightfile_warm" and x["gold_hit"])},
        "reading": "warm cache_n >> 0 AND cold gold_hits == 0 AND control leaks == 0 AND "
                   "positive gold_hits == repeats is the pass shape; anything else is a finding, "
                   "not a rounding error",
    }
    with open(a.out + ".metrics.json", "w") as f:
        json.dump({"ask": a.ask, "gold": a.gold, "repeats": a.repeats,
                   "cartridge": cart, "metrics": metrics}, f, indent=1)
    print(json.dumps(metrics, indent=1))
    if metrics["control"]["leaks"]:
        print("[m3c] CONTROL FAILED: knowledge appeared from the wrong cartridge with no text - "
              "the venue stops here until diagnosed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
