#!/usr/bin/env python3
"""m4_reason - do DavidAU's {REASON:} modes change TASK OUTCOMES, measured on a live server?

The feature shipped on a promise the README makes and nobody on this box has tested: that the
persona framings (coder, house, deeptree, ...) beat the default framing on real work. The fork's
own law - no claims without a measurement - means the docs cannot say "modes help" until this
tool has run on THIS build, THIS day, against the same server every arm rides.

Method, and why each piece is what it is:

  * Arms are selected with DavidAU's own {REASON:<mode>} tag appended to the probe text. The tag
    outranks every server level (the template's l_car rule, pinned by serve/test_reason.py), so
    the sweep never POSTs /reason, never moves the sticky map, never disturbs the box it measures.
    The special arm "default" sends no tag at all - that is what every ordinary request gets
    today, whatever the config's reason_mode is.
  * Temperature 0, one server, probes interleaved by arm - the same-day, same-build discipline the
    contributing guide demands. Memory is muted per request (X-Strata-Memory: off): recall noise
    is constant across arms but nonzero, and muting it is one fewer moving part.
  * Scoring is DETERMINISTIC: every probe carries a checker (exact answer match, code exec'd in a
    subprocess against hidden asserts, checklist regexes). No LLM judge, so no self-preference
    bias and a rerun reads identical bytes from the graders.
  * NOISE FLOOR: "default" and "ultra" are the SAME framing on a box whose default is ultra. The
    score gap between them is the run-to-run spread of the whole instrument at temp 0 (MoE routing
    and batch shape are the only non-determinism left). The docs may only claim a mode "helps" by
    more than that spread - and never less than the +/-0.05 band the venue work uses.

  Report: one table, arms x shapes, mean score per cell, plus per-arm truncation counts (a mode
  that rambles past max_tokens fails its own probe - verbosity is part of the cost, measured).

    python3 tools/m4_reason.py --pilot            # quick smoke: 3 shapes, 4 arms, one pass
    python3 tools/m4_reason.py --json out.json    # the full sweep, 7 shapes x 15 probes x 2 passes
    python3 tools/m4_bench_fetch.py               # round 2: fetch the published-benchmark bank
    python3 tools/m4_reason.py --bank r2 --arms default --repeats 1   # calibrate saturation first
"""
from __future__ import annotations

import argparse, http.client, json, re, subprocess, sys, time, urllib.request
from pathlib import Path

HOST = "http://127.0.0.1:8005"

# ---------------------------------------------------------------- checkers (deterministic, by design)

def _last_int(text):
    ints = re.findall(r"-?\d+", text or "")
    return int(ints[-1]) if ints else None

def _check_int(expected):
    def f(text):
        return 1.0 if _last_int(text) == expected else 0.0
    return f

def _check_name(expected, pool):
    """The answer is the name the reply commits to LAST - graders read answers at the end."""
    def f(text):
        t = (text or "").lower()
        hits = [(t.rfind(n.lower()), n.lower()) for n in pool if re.search(r"\b" + re.escape(n.lower()) + r"\b", t)]
        return 1.0 if hits and max(hits)[1] == expected.lower() else 0.0
    return f

def _pyblock(text):
    """The model's python: a fenced ```python block, else any fence, else the whole reply."""
    m = re.search(r"```python\n(.*?)```", text or "", re.S)
    if not m:
        m = re.search(r"```\w*\n(.*?)```", text or "", re.S)
    return m.group(1) if m else (text or "")

def _check_py(tests):
    """Exec the candidate code, then the hidden asserts, in a fresh subprocess: pass = 1.0."""
    def f(text):
        code = _pyblock(text)
        if not code.strip():
            return 0.0
        try:
            r = subprocess.run([sys.executable, "-c", code + "\n" + tests],
                               capture_output=True, text=True, timeout=10)
            return 1.0 if r.returncode == 0 else 0.0
        except subprocess.TimeoutExpired:
            return 0.0
    return f

def _check_regexes(checks):
    """Partial credit: mean over (name, predicate-on-lowered-text) pairs."""
    def f(text):
        t = text or ""
        got = [1.0 if p(t) else 0.0 for _, p in checks]
        return sum(got) / len(got) if got else 0.0
    return f

def _check_json(expected):
    """Reply must parse as JSON equal to expected (names/keys as sets where the probe says so)."""
    def f(text):
        t = (text or "").strip()
        m = re.search(r"[\[{].*[\]}]", t, re.S)
        if not m:
            return 0.0
        try:
            got = json.loads(m.group(0))
        except ValueError:
            return 0.0
        if isinstance(expected, (list, tuple)):
            return 1.0 if isinstance(got, list) and sorted(map(str, got)) == sorted(map(str, expected)) else 0.0
        return 1.0 if got == expected else 0.0
    return f

# ---------------------------------------------------------------- the probe bank
# Every text is self-contained and uses invented, memory-safe nouns; the header mutes recall
# anyway. check scores 0.0..1.0.

def probes():
    P = []
    add = lambda shape, prompt, check: P.append({"shape": shape, "prompt": prompt, "check": check})

    # math: floor-division word problems, answer = one integer
    add("math", "A machine packs 7 crates with 13 boxes each. The boxes are re-packed into crates "
                "holding 9 boxes each, and the last crate is deliberately left incomplete. How many "
                "COMPLETE crates of 9 are there? Answer with just the number.", _check_int(10))
    add("math", "A baker makes 8 trays of 15 muffins. She boxes the muffins into gift boxes of 9, "
                "with the final box deliberately incomplete. How many FULL gift boxes does she "
                "fill? Answer with just the number.", _check_int(13))
    add("math", "A school takes 5 groups of 17 students to a museum. Each bus seats exactly 6 "
                "students, and the last bus may run partly empty. How many FULL buses do they "
                "need? Answer with just the number.", _check_int(14))

    # logic: ordering puzzles, answer = one name/word
    # (the sweep caught this grader bug, 2026-10-17: Ana > Bea > Cy > Dee, the youngest is Dee, and
    # EVERY arm answered Dee correctly 16/16 while the bank expected Cy - the model was right, the
    # expected value was wrong. Fixed in the bank; the saved run rescores offline, no regeneration.)
    add("logic", "Bea is older than Cy. Dee is younger than Cy. Ana is older than Bea. Who is the "
                 "youngest? Answer with just the name.", _check_name("Dee", ["Bea", "Cy", "Dee", "Ana"]))
    add("logic", "On a shelf the red book sits left of the blue. The green sits right of the blue. "
                 "The black sits left of the red. Which book is furthest right? Answer with just "
                 "the color.", _check_name("green", ["red", "blue", "green", "black"]))
    add("logic", "A planner ranks chores: sweep above mop, mop above dust, polish above sweep. "
                 "Which chore ranks lowest? Answer with just the chore.", _check_name("dust", ["sweep", "mop", "dust", "polish"]))

    # code: write functions, graded by hidden asserts
    add("code", "Write Python: a function count_v2(n) returning how many whole multiples of 2 fit "
                "in n, and a function add7(x) returning x+7. Reply with just the code.",
        _check_py("assert count_v2(9) == 4\nassert count_v2(10) == 5\nassert add7(3) == 10\nassert add7(-7) == 0\nprint('ok')"))
    add("code", "Write Python: a function top_pair(nums) returning the sum of the two largest "
                "DISTINCT values in the list (at least two distinct values guaranteed). Reply with "
                "just the code.",
        _check_py("assert top_pair([1,2,3]) == 5\nassert top_pair([4,4,9]) == 13\nassert top_pair([5,9,9]) == 14\nassert top_pair([-1,-2,-3]) == -3\nprint('ok')"))

    # debug: find and fix the planted bug, graded by the same unit the symptom quotes
    d1 = ("The function below is buggy: avg([2, 4]) returns 2, but it should return 3.\n\n"
          "```python\ndef avg(nums):\n    total = 0\n    for n in nums:\n        total += n\n"
          "    return total / len(nums) - 1\n```\n\nReply with the FIXED function only, as one "
          "python code block.")
    add("debug", d1, _check_py("assert avg([2,4]) == 3\nassert avg([1,2,3]) == 2\nprint('ok')"))
    d2 = ("The function below is buggy: count_evens([2, 3, 4]) returns 1, but it should return 2.\n\n"
          "```python\ndef count_evens(nums):\n    c = 0\n    for i in range(1, len(nums)):\n"
          "        if nums[i] % 2 == 0:\n            c += 1\n    return c\n```\n\nReply with the "
          "FIXED function only, as one python code block.")
    add("debug", d2, _check_py("assert count_evens([2,3,4]) == 2\nassert count_evens([1,1,1]) == 0\nprint('ok')"))

    # plan: multi-constraint plans, graded by checklist regexes (partial credit). The numeric
    # checks look for the number NEAR its constraint word - "total 380" counts, a stray 12 in a
    # dinner cost does not make a 90-zid total look legal.
    yes = lambda pat: (lambda t: re.search(pat, t, re.I | re.S) is not None)
    def ctx_under(words, cap):
        rx = re.compile(r"(?:" + words + r")[^0-9]{0,25}(\d+)", re.I)
        return lambda t: any(int(m.group(1)) <= cap for m in rx.finditer(t or ""))
    p1 = ("Plan a 2-day trip: Day 1 must include a museum visit; total budget 400 zids with no "
          "single night over 120 zids; no flying; exactly one hotel stay. Write the plan as a "
          "Day 1 line and a Day 2 line, each with its costs.")
    add("plan", p1, _check_regexes([
        ("day1", yes(r"day\s*1")), ("day2", yes(r"day\s*2")),
        ("museum on day 1", yes(r"day\s*1(?:(?!day\s*2).)*museum")),
        ("ground transport present", yes(r"\b(train|bus|car|drive|ferry|walk|bike|boat)\b")),
        ("total <= 400", ctx_under(r"total|budget|overall|spend", 400)),
        ("night <= 120", ctx_under(r"hotel|night|room|stay", 120))] ))
    p2 = ("Plan a week of dinners: exactly 5 dinners, all vegetarian, at most 2 may contain dairy, "
          "total cost under 60 zids, and at least one dish must use beans. List each dinner on its "
          "own line with its cost.")
    add("plan", p2, _check_regexes([
        ("5 lines", lambda t: sum(1 for ln in (t or "").splitlines() if re.search(r"\d", ln)) >= 5),
        ("no meat words", yes(r"^\s*(?![\s\S]*\b(chicken|beef|pork|fish|lamb|bacon|tuna)\b)")),
        ("beans", yes(r"\bbean")),
        ("total under 60", ctx_under(r"total|under|budget|overall|cost", 59)),
        ("dairy rule honored", yes(r"dairy"))] ))

    # summarize: one sentence, hard word cap, must keep the facts
    s1 = ("Summarize the following in ONE sentence of at most 25 words.\n\n"
          "The Quarrington Bridge collapsed in 1976 after a support pier cracked during the winter "
          "floods, killing four ferry workers who had stayed late to secure the cables. The county "
          "created the Vaudry Repair Fund the following spring, financed by a toll on river barge "
          "traffic, and the rebuilt bridge reopened in 1981 with wider lanes and a reinforced pier.")
    kw = [("bridge/collapse", yes(r"bridge|collapse")), ("1976", yes(r"1976")),
          ("cause", yes(r"flood|pier|crack|winter")), ("fund or reopening", yes(r"fund|vaudry|1981|reopened"))]
    add("summarize", s1, _check_regexes([("word cap", lambda t: len((t or "").split()) <= 25)] + kw))
    s2 = ("Summarize the following in ONE sentence of at most 25 words.\n\n"
          "In 2011 the lighthouse keeper Wren Odessey refused to automate his post on Sable Rock, "
          "arguing the gull-flock landings fooled every early sensor into false alarms. His "
          "objection delayed the board's retrofit plan by two years; the retrofit finally shipped "
          "in 2013 with gull-trained spotters kept as a manual fallback.")
    kw2 = [("lighthouse/keeper", yes(r"lighthouse|keeper|sable")), ("2011", yes(r"2011")),
           ("refusal reason", yes(r"gull|sensor|alarm|automat")), ("outcome", yes(r"2013|retrofit|delay"))]
    add("summarize", s2, _check_regexes([("word cap", lambda t: len((t or "").split()) <= 25)] + kw2))

    # extract: strict JSON output under framing that likes to add prose
    e1 = ("Read the note and reply ONLY with a JSON array of the four people's names, no prose.\n\n"
          "At the opening, Mirella greeted Fenwick, while Ovid and Petra examined the mosaic; the "
          "mayor thanked all four for restoring the fountain.")
    add("extract", e1, _check_json(["Mirella", "Fenwick", "Ovid", "Petra"]))
    e2 = ("Read the note and reply ONLY with a JSON object mapping each town to its population as "
          "a number, no prose.\n\nThe census clerk recorded: Brackenford held 1,204 souls; Yarrow "
          "Fell, 887; and Millhudder, 2,310.")
    add("extract", e2, _check_json({"Brackenford": 1204, "Yarrow Fell": 887, "Millhudder": 2310}))
    return P

# ---------------------------------------------------------------- the instrument

def ask(host, arm, prompt, max_tokens, timeout):
    """One generation. The tag arm rides the message; 'default' sends the bare probe. Returns the
    final-answer text (content; reasoning_content only if the writer put the answer in its
    thinking), plus finish_reason and usage for the truncation column."""
    text = prompt if arm == "default" else prompt + "\n\n{REASON:" + arm + "}"
    body = json.dumps({"model": "m", "temperature": 0, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": text}]}).encode()
    url = host.rstrip("/") + "/v1/chat/completions"
    # the box is shared: this harness, the operator's own sessions and whatever the engine is
    # mid-reading. A connection dropped under the queue (measured 2026-10-17: RemoteDisconnected
    # on probe 2 of a GPQA calibration, engine healthy throughout) is a round to retry, not a
    # sweep to lose - three tries, 10 s apart, then the exception rides like any other failure.
    for attempt in range(3):
        try:
            t0 = time.time()
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json",
                                                  "X-Strata-Memory": "off"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode())
            break
        except (http.client.RemoteDisconnected, ConnectionError) as ex:
            if attempt == 2:
                raise
            time.sleep(10)
    ch = d["choices"][0]
    m = ch["message"]
    ans = (m.get("content") or "").strip() or (m.get("reasoning_content") or "").strip()
    return {"text": ans, "finish": ch.get("finish_reason"), "usage": d.get("usage", {}),
            "secs": round(time.time() - t0, 1)}

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--arms", default="", help="comma list; 'default' = no tag (today's bytes). "
                    "The shipped set: default,ultra,off,xhigh,coder,house,deeptree,socrates")
    ap.add_argument("--shapes", default="", help="comma list of shapes (default: all seven)")
    ap.add_argument("--repeats", type=int, default=2, help="passes over the whole probe bank per arm")
    ap.add_argument("--max-tokens", type=int, default=3072)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--bank", choices=("r1", "r2"), default="r1", help="r1 = the built-in probes; "
                    "r2 = the published-benchmark bank (tools/m4_bank_r2.py, fetch first with "
                    "tools/m4_bench_fetch.py)")
    ap.add_argument("--bench-dir", default="", help="r2 data dir (default Strata-data/bench)")
    ap.add_argument("--force", action="store_true", help="r2: load even if file hashes moved")
    ap.add_argument("--pilot", action="store_true", help="smoke preset: math,code,plan x "
                    "default,ultra,coder,house x 1 pass")
    ap.add_argument("--json", default="", help="write every record here")
    a = ap.parse_args()

    if a.bank == "r2":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from m4_bank_r2 import probes as bank_probes
        bank_all = bank_probes(a.bench_dir or None, force=a.force)
    else:
        bank_all = probes()
    bank_shapes = list(dict.fromkeys(p["shape"] for p in bank_all))

    arms = (a.arms or ("default,ultra,coder,house" if a.pilot else
                       "default,ultra,off,xhigh,coder,house,deeptree,socrates")).split(",")
    shapes = (a.shapes or ("math,code,plan" if a.pilot else ",".join(bank_shapes))).split(",")
    bank = [p for p in bank_all if p["shape"] in shapes]

    print(f"m4_reason: {len(arms)} arms x {len(bank)} probes x {a.repeats} passes "
          f"= {len(arms)*len(bank)*a.repeats} generations at temperature 0 on {a.host} "
          f"(memory muted per request)")
    recs, t_start = [], time.time()
    for rep in range(a.repeats):
        for p in bank:
            for arm in arms:                       # interleaved: every arm sees the same hour of the box
                r = ask(a.host, arm, p["prompt"], a.max_tokens, a.timeout)
                score = p["check"](r["text"])
                r.update(arm=arm, shape=p["shape"], pass_no=rep, score=round(score, 3),
                         gold=str(p.get("gold", ""))[:200], prompt_chars=len(p["prompt"]))
                recs.append(r)
                print(f"  pass {rep+1} {p['shape']:<10} {arm:<9} {score:.2f} "
                      f"({r['secs']}s, {r['usage'].get('completion_tokens', '?')} tok, {r['finish']})",
                      flush=True)

    # aggregate: mean per arm x shape, mean per arm, truncations per arm
    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0
    by = {}
    for r in recs:
        by.setdefault((r["arm"], r["shape"]), []).append(r["score"])
    print(f"\narm          " + "".join(f"{s:>11}" for s in shapes) + f"{'OVERALL':>10}{'trunc':>6}")
    table = {}
    for arm in arms:
        cells = [mean(by.get((arm, s), [])) for s in shapes]
        overall = mean([r["score"] for r in recs if r["arm"] == arm])
        trunc = sum(1 for r in recs if r["arm"] == arm and r["finish"] == "length")
        table[arm] = {"shapes": dict(zip(shapes, [round(c, 3) for c in cells])),
                      "overall": round(overall, 3), "truncations": trunc}
        print(f"{arm:<12}" + "".join(f"{c:>11.2f}" for c in cells) + f"{overall:>10.2f}{trunc:>6}")

    if "default" in arms and "ultra" in table and "ultra" in arms:
        floor = abs(table["default"]["overall"] - table["ultra"]["overall"])
        print(f"\nnoise floor: |default - ultra| = {floor:.3f} (same framing on this box - the "
              f"instrument's own spread at temp 0).\nClaims may only be made over that spread, "
              f"and never under the +/-0.05 venue band.")
    for arm in arms:
        if arm in ("default",):
            continue
        gap = table[arm]["overall"] - table.get("default", table[arm])["overall"]
        print(f"  {arm:<10} vs default: {gap:+.3f}")
    print(f"\ntotal wall time: {(time.time()-t_start)/60:.1f} min")

    if a.json:
        Path(a.json).write_text(json.dumps({"arms": arms, "shapes": shapes, "repeats": a.repeats,
                                            "table": table, "records": recs}, indent=1))
        print(f"wrote {a.json}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
