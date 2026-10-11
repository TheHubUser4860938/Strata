#!/usr/bin/env python3
"""spikes/window/needle_393k.py - the T6 acceptance long-range recall probe for a YaRN change.

The context decision changes the YaRN factor (2 -> 1.5) along with the window (524288 -> 393216);
upstream's yarn numbers are one machine / one quant and its own doc calls the feature experimental,
so the candidate arm only wins if long-range recall survives at the new factor ON THIS BOX.

  haystack: the 100 eval_questions.csv lines (real English) shuffled with seed 20261011 and
            repeated until the prompt passes --depth tokens;
  needle:   one invented fact at --plant-at (default 60% depth) with a per-run nonce codeword
            ("The maintenance hatch of the Kelvarn platform is sealed with codeword amberNNNNN.");
  question: the LAST user message; memory MUTED (X-Strata-Memory: off) so the store's 100 facts
            can neither leak nor pad;
  judge:    strict word-boundary match on the nonce (no world-knowledge path to a nonce);
  guard:    usage.prompt_tokens must sit under the server's max_context with room for the answer,
            and within +-5% of the local estimate. Calibration note (measured 2026-10-11): the
            bench's 1.08 tok/word fits its prose corpus; THIS filler (question lines) measures
            1.20 tok/word (358,906 live vs 300,457 estimated at 1.08) - the constant below uses
            the measured value so depth labels are honest.

A VOID guard voids the PASS, not the answer: report both.
Usage: .venv/bin/python spikes/window/needle_393k.py --url http://127.0.0.1:8005 --depth 300000 --tag yarn15-393k
"""
import argparse, json, random, re, sys, time, urllib.request as U
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from gate1_driver import rows  # noqa: E402

TOK_PER_WORD = 1.20    # measured 2026-10-11 on this exact filler (358906 ptok / 299k words + needle)
FILLER = [r["question"] for r in rows()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--depth", type=int, default=300000, help="approx haystack tokens (x TOK_PER_WORD)")
    ap.add_argument("--plant-at", type=float, default=0.6)
    ap.add_argument("--tag", default="yarn15-393k")
    a = ap.parse_args()
    nonce = format(int(time.time()) % 100000, "05d")
    words, rnd = [], random.Random(20261011)
    while sum(len(w.split()) for w in words) * TOK_PER_WORD < a.depth:
        block = list(FILLER)
        rnd.shuffle(block)
        words += block
    needle = f"The maintenance hatch of the Kelvarn platform is sealed with codeword amber{nonce}."
    at = int(len(words) * a.plant_at)
    words.insert(at, needle)
    hay = " ".join(words)
    q = ("And the answer: what codeword seals the maintenance hatch of the Kelvarn platform? "
         "Answer with just the codeword.")
    body = {"messages": [{"role": "user", "content": hay + "\n\n" + q}],
            "temperature": 0.8, "reasoning_effort": "off", "max_tokens": 40}
    approx = sum(len(w.split()) for w in words) * TOK_PER_WORD
    req = U.Request(a.url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                    method="POST", headers={"Content-Type": "application/json", "X-Strata-Memory": "off"})
    t0 = time.time()
    with U.urlopen(req, timeout=1800) as r:
        j = json.loads(r.read())
    secs = time.time() - t0
    ans = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    ptok = (j.get("usage") or {}).get("prompt_tokens") or 0
    ok = bool(re.search(r"(?<!\w)amber" + nonce + r"(?!\w)", ans.lower()))
    guard = abs(ptok - approx) <= approx * 0.05
    line = (f"needle {a.tag}: built ~{approx:.0f} tok, live ptok {ptok} (guard "
            f"{'OK' if guard else f'VOID - estimate off by {100*(ptok-approx)/approx:+.1f}%'}), "
            f"planted at line {at}/{len(words)} (~{at*100//len(words)}% depth = position ~{ptok*at//len(words)}), "
            f"nonce amber{nonce}, wall {secs:.0f} s, answer {ans[:40]!r} -> "
            f"{'FOUND' if ok else 'MISSED'}{'' if guard or not ok else ' (guard VOID: depth label soft, answer stands)'}")
    print(line)
    (HERE / "state" / f"needle_{a.tag}.txt").write_text(line + "\n")


if __name__ == "__main__":
    main()
