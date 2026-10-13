#!/usr/bin/env python3
"""Clean adjacent M2d probe: one process, fresh ask then its continuation, nothing else on the
port. The venue's M2d line was contaminated in BOTH runs (4 and 5) because this assistant's own
harness issued a title-generation request between the two asks - the slot-replacement law, not
block instability. The head block is pinned by the FIRST user message (block_for: h+sha1(first
user)), so it is byte-stable across a conversation; a continuation should therefore reuse the
whole parent prompt. This probe measures it with the session idle (launched backgrounded, then
the assistant ends its turn so no generation fires between the pair). Prints only scrub-safe
numbers; no template bytes leave the process."""
import json
import sys
import time
import urllib.request as U

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8005"
MODEL = "qwen3.8-flash-next-iq3_s"
ASK = "What graphics card does this machine have? Answer in a few words."
FOLLOW = "Name one color. One word only."


def post(msgs):
    body = {"model": MODEL, "messages": msgs, "max_tokens": 500, "temperature": 0.8,
            "reasoning_effort": "off"}
    req = U.Request(URL + "/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                    headers={"Content-Type": "application/json"})
    t0 = time.time()
    with U.urlopen(req, timeout=240) as r:
        d = json.loads(r.read().decode())
    t = d.get("timings") or {}
    return {"ptok": d["usage"]["prompt_tokens"], "cache_n": t.get("cache_n"),
            "prompt_ms": t.get("prompt_ms"), "s": round(time.time() - t0, 2),
            "reply": (d["choices"][0]["message"].get("content") or "").strip()}


# fresh turn: one user message (the head block pins on it)
f = post([{"role": "user", "content": ASK}])
# continuation: the SAME conversation + assistant answer + a follow-up - a strict extension of
# the fresh prompt, so a stable head block means cache_n ~= fresh ptok
c = post([{"role": "user", "content": ASK}, {"role": "assistant", "content": f["reply"] or "it has a card"},
          {"role": "user", "content": FOLLOW}])
print("FRESH ", json.dumps(f))
print("CONT  ", json.dumps(c))
verdict = "REUSE (head stable, lever already works)" if (c["cache_n"] or 0) >= 0.8 * f["ptok"] \
    else "NO REUSE (slot replaced or head moved - investigate)"
print(f"VERDICT: {verdict} | reused {c['cache_n']}/{f['ptok']} = "
      f"{100 * (c['cache_n'] or 0) / max(f['ptok'], 1):.0f}% of the parent prompt")
