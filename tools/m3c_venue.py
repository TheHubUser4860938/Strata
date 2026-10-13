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

M1b the pin law, probed - restore the spike-era file (c5_comp.bin): the current engine
    must REFUSE it ("saved with another engine configuration"), which is the cartridge's
    safety story passing. A 200 here is the surprise worth stopping for.

M2  warm vs cold - the same gold question with memory on (the standing prefix rides) and
    with X-Strata-Memory: off (cold). The response's own timings carry the engine's view:
    prompt_ms and cache_n (tokens the engine did NOT re-read). The delta is the claim;
    the record states it in ms and tokens, not adjectives. Measured 2026-10-13 and kept in
    the file so the next boot's experiment has a before: a FRESH conversation after
    restore re-reads its whole prompt (reused 0, full prompt_ms) - the restore is fast but
    the fresh-conversation time win is not yet earned.

M2d the continuation control - the same conversation with a follow-up turn: engine resume
    re-reads only the new tail. M2 + M2d together locate the mechanism: reuse today is
    same-conversation; cross-conversation prefix inheritance is the engine's RAM-side
    conversation cache (--conversation-cache-mib) - the next experiment, not tonight.

M3  negative control - knowledge never leaks from a loaded cartridge alone. The wrong
    cartridge is made LIVE: a foreign muted conversation (photosynthesis only) is run and
    SAVED - same pin, different knowledge - then restored, and the gold question asked
    muted: standing text nowhere in the prompt, slot holding a stranger's chat. The honest
    answer is the unknown-marker; a gold word there is a LEAK, which exits 2 and stops the
    venue. Then the right file returns and the positive control runs warm.

The gold question and answer are operator arguments (--ask/--gold): point them at a real
standing fact the store carries (the script prints the first USR/DEC rows to help). Output:
scrubbed JSONL lines + a metrics json, in the repo's venue-record shape.
"""
from __future__ import annotations

import argparse, datetime, json, re, sys, time, urllib.error, urllib.parse, urllib.request as U


def scrub(s: str) -> str:
    return (s or "").replace("<|", "< |").replace("|>", "| >")


def call(url: str, path: str, body: dict | None = None, headers: dict | None = None,
         method: str = "POST", timeout: float = 300.0):
    data = json.dumps(body).encode() if body is not None else None
    req = U.Request(url + path, data=data, method=method,
                    headers={"Content-Type": "application/json", **(headers or {})})
    with U.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


ANSWER_RULE = ("Answer in a few words from what you know. If you do not know, reply exactly: idk")


def final_line(h: str) -> str:
    """Few-word answers are one line; verbose ones end with the answer. Scoring the whole
    text was fooled live (2026-10-13) by an EXAMPLE inside a clarification - 'Custom PC with
    an NVIDIA RTX 4090' while the model admitted it did not know the machine - a false leak
    that correctly stopped the venue and correctly demanded a better criterion."""
    return (h or "").strip().split("\n")[-1].lower()


def ask(url: str, model: str, prompt: str, muted: bool) -> dict:
    """One fresh conversation, one answer; the response's own timings (engine view: prompt_ms,
    cache_n = tokens not re-read) ride back with it - the measurement never touches a log."""
    hdrs = {"X-Strata-Memory": "off"} if muted else {}
    r = call(url, "/v1/chat/completions",
             {"model": model, "messages": [{"role": "user", "content": f"{prompt}\n\n{ANSWER_RULE}"}],
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
    if cart.get("state") == "pending":
        # "pending" is not a failure - it is the law saying "the next standing request
        # restores". Be that request: one tiny warm-up ask, then re-read the view. (A
        # textless or muted request - a harness title call, an operator probe - invalidates
        # the slot honestly, and it is luck of timing whether the venue finds it loaded.)
        ask(a.url, a.model, "warm the cartridge", muted=False)
        cart = (call(a.url, "/memory?limit=1", method="GET", timeout=30)["stats"].get("cartridges") or {})
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
             "restore_ms": (r.get("timings") or {}).get("restore_ms"), "tokens": r.get("n_restored")})

    # M1b: the pin law, probed not assumed - the spike-era file must be REFUSED (it was saved
    # by another engine configuration). A 400 here is the cartridge's safety story passing:
    # an engine upgrade costs one re-seed, never a stale read. A 200 would be the surprise.
    try:
        r = call(a.url, "/slots/0?action=restore", {"filename": a.control_file})
        rec({"m": "M1b_pin", "file": a.control_file, "refused": False,
             "note": "expected a pin refusal; this engine ACCEPTED a foreign file - investigate"})
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        rec({"m": "M1b_pin", "file": a.control_file, "refused": e.code == 400, "code": e.code, "body": body})

    # M2: warm vs cold, interleaved so drift hits both sides equally.
    for rep in range(a.repeats):
        for muted in (False, True):
            r = ask(a.url, a.model, a.ask, muted)
            rec({"m": "M2_warm" if not muted else "M2_cold", "rep": rep, "gold_hit":
                 a.gold.lower() in final_line(r["hypothesis"]), **r})

    # M2d: the continuation control, run as an ADJACENT pair so nothing else touches the slot
    # between the two asks - engine resume is strict extension of the last prompt the slot saw,
    # and a cold ask in between would silently invalidate the whole probe (it did: the first
    # venue's non-adjacent M2d measured full prefill and looked, wrongly, like resume failing).
    # Fresh ask -> full prompt_ms; the same conversation + one follow-up -> resume re-reads only
    # the tail. The pair locates the mechanism in the record: reuse today is same-conversation;
    # cross-conversation prefix inheritance is the conversation-cache experiment (M3e, boot 14+).
    d0 = ask(a.url, a.model, a.ask, muted=False)
    d1 = None
    if d0["hypothesis"]:
        cont = {"model": a.model, "messages": [{"role": "user", "content": a.ask},
                                               {"role": "assistant", "content": d0["hypothesis"]},
                                               {"role": "user", "content": "Name one color. One word only."}],
                "max_tokens": 500, "temperature": 0.8, "reasoning_effort": "off"}
        rc = call(a.url, "/v1/chat/completions", cont)
        t = rc.get("timings") or {}
        d1 = {"prompt_tokens": (rc.get("usage") or {}).get("prompt_tokens"), "cache_n": t.get("cache_n"),
              "prompt_ms": t.get("prompt_ms"),
              "hypothesis": scrub((rc["choices"][0]["message"].get("content") or "").strip())}
    rec({"m": "M2d_fresh", **d0})
    rec({"m": "M2d_cont", **d1} if d1 else {"m": "M2d_cont", "note": "empty fresh answer"})

    # M3: the wrong cartridge, made live: a foreign conversation (muted, so it carries no
    # standing text) is run and SAVED - same pin, different knowledge - then restored, and the
    # gold question asked MUTED: standing text nowhere in the prompt, the slot holding a
    # photosynthesis chat. The honest answer is the unknown-marker; a gold word is a LEAK.
    for rep in range(a.repeats):
        ctrl = f"MEMCTRL-{datetime.datetime.now().strftime('%H%M%S')}-{rep}.bin"
        call(a.url, "/v1/chat/completions",
             {"model": a.model, "messages": [{"role": "user",
                                              "content": "Today we only discuss photosynthesis. Reply OK."}],
              "max_tokens": 500, "temperature": 0.8, "reasoning_effort": "off"},
             headers={"X-Strata-Memory": "off"})
        call(a.url, "/slots/0?action=save", {"filename": ctrl})
        call(a.url, "/slots/0?action=restore", {"filename": ctrl})
        r = ask(a.url, a.model, a.ask, muted=True)
        leaked = a.gold.lower() in final_line(r["hypothesis"])
        rec({"m": "M3_wrongcartridge_muted", "rep": rep, "file": ctrl, "leaked": leaked,
             "says_unknown": a.unknown.lower() in final_line(r["hypothesis"]), **r})
    for rep in range(a.repeats):
        call(a.url, "/slots/0?action=restore", {"filename": cart["file"]})
        r = ask(a.url, a.model, a.ask, muted=False)
        rec({"m": "M3_rightfile_warm", "rep": rep, "gold_hit":
             a.gold.lower() in final_line(r["hypothesis"]), **r})
    out.close()

    rows = [json.loads(l) for l in open(a.out)]

    def med(m, key):
        vs = sorted(x[key] for x in rows if x["m"] == m and isinstance(x.get(key), (int, float)))
        return round(vs[len(vs) // 2], 1) if vs else None

    metrics = {
        "restore_ms_median": med("M1_restore", "restore_ms"),
        "pin": {"refused": sum(1 for x in rows if x["m"] == "M1b_pin" and x.get("refused")),
                "accepted_foreign": sum(1 for x in rows if x["m"] == "M1b_pin"
                                        and x.get("refused") is False)},
        "warm": {"prompt_ms_median": med("M2_warm", "prompt_ms"),
                 "cache_n_median": med("M2_warm", "cache_n"),
                 "gold_hits": sum(1 for x in rows if x["m"] == "M2_warm" and x["gold_hit"])},
        "cold": {"prompt_ms_median": med("M2_cold", "prompt_ms"),
                 "cache_n_median": med("M2_cold", "cache_n"),
                 "gold_hits": sum(1 for x in rows if x["m"] == "M2_cold" and x["gold_hit"])},
        "continuation": {"fresh_prompt_ms": med("M2d_fresh", "prompt_ms"),
                         "cont_prompt_ms": med("M2d_cont", "prompt_ms"),
                         "cont_cache_n": med("M2d_cont", "cache_n")},
        "control": {"leaks": sum(1 for x in rows if x["m"] == "M3_wrongcartridge_muted" and x["leaked"]),
                    "unknowns": sum(1 for x in rows if x["m"] == "M3_wrongcartridge_muted"
                                    and x["says_unknown"])},
        "positive": {"gold_hits": sum(1 for x in rows if x["m"] == "M3_rightfile_warm" and x["gold_hit"])},
        "reading": "PASS shape: pin refused == 1, control leaks == 0, positive gold_hits == repeats, "
                   "cold gold_hits == 0. The TIME claim reads from warm prompt_ms vs continuation "
                   "prompt_ms and the M1 restore_ms: as measured 2026-10-13, a FRESH conversation "
                   "after restore re-reads its whole prompt (reused 0), so the fresh-conversation "
                   "time win is NOT yet earned - continuation reuse works (engine resume, same "
                   "conversation), cross-conversation prefix inheritance is the conversation-cache "
                   "experiment (--conversation-cache-mib, boot 14+). Anything else is a finding, "
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
