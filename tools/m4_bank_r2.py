#!/usr/bin/env python3
"""m4_bank_r2 - the round-2 probe bank: published benchmarks, deterministic scoring.

Round 1 (built-in probes) saturated: six of seven shapes answered 1.00 by every arm, so the
instrument could not see a mode effect even if one existed. This bank replaces hand-written
probes with four published suites, chosen for the two properties the fork's law demands -

  * DETERMINISTIC scoring: GSM-Symbolic gold is the number after "#### " (exact numeric match);
    MATH-500 gold is its "answer" field (normalized string or numeric equality); HumanEval is
    scored by EXECUTION - the canonical unit tests, in a fresh subprocess, pass/fail, no regex;
    BIG-Bench Hard targets are short strings (a letter, True/False, an integer, valid/invalid),
    matched at the tail of the reply. No LLM judge anywhere: reruns grade identical bytes.
  * PUBLISHED HEADROOM: GSM-Symbolic exists because models that aced GSM8K lost 7-15 points on
    the templated reroll (memorization); MATH-500 is competition math; BBH is the set where
    chain-of-thought was discovered and where small models still miss 20-40%. Saturation cannot
    hide the effect this time - if a mode helps, these shapes can see it.

Sampling is a FIXED STRIDE over file order (no RNG, no seed drift): a rerun reads the exact same
76 probes. The bank refuses to load files whose sha256 differs from manifest.json unless --force,
so a silently-changed corpus cannot move the numbers between runs.

GPQA diamond joined on 2026-10-17: the origin is gated and CC BY-NC 4.0, the owner's HF account was
granted access, and the token sits in a chmod-600 file in the data dir - NEVER in the memory store,
where a secret would sit plaintext and ride back into every recall.
"""
from __future__ import annotations

import csv, gzip, hashlib, json, random, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BBH = {  # task -> (shape, instruction, checker kind)
    "boolean_expressions":              ("bbh_bool",    "Answer with just True or False.", "tf"),
    "multistep_arithmetic_two":         ("bbh_arith",   "Answer with just the number.",    "int"),
    "date_understanding":               ("bbh_date",    "Answer with just the letter.",    "letter"),
    "logical_deduction_seven_objects":  ("bbh_deduce",  "Answer with just the letter.",    "letter"),
    "formal_fallacies":                 ("bbh_fallacy", "Answer with just valid or invalid.", "word"),
}

def _num(s):
    s = (s or "").replace(",", "").strip().rstrip(".")
    try:
        return float(s)
    except ValueError:
        return None

def _last_num(text):
    hits = re.findall(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text or "")
    return _num(hits[-1]) if hits else None

def _check_gsm(gold):
    want = _num(gold)
    return lambda t: 1.0 if want is not None and _last_num(t) == want else 0.0

def _norm_math(s):
    s = (s or "").strip().lower()
    for junk in ("\\left", "\\right", "\\!", "\\ ", "$", " "):
        s = s.replace(junk, "")
    return s.strip().rstrip(".")

def _check_math(gold):
    g = _norm_math(gold)
    def f(t):
        m = re.findall(r"\\boxed\{((?:[^{}]|\{[^{}]*\})*)\}", t or "")
        got = _norm_math(m[-1]) if m else ""
        if got == g:
            return 1.0
        if got and _num(got) is not None and _num(g) is not None and _num(got) == _num(g):
            return 1.0
        if not m and _last_num(t) is not None and _num(g) is not None and _last_num(t) == _num(g):
            return 1.0                       # no box but the right number: the mode answered, just not to format
        return 0.0
    return f

def _check_humaneval(prompt, test, entry):
    """Execution-graded: prompt + candidate + the canonical test, in a fresh subprocess."""
    def f(t):
        cand = t or ""
        m = re.search(r"```(?:python)?\n(.*?)```", cand, re.S)
        if m:
            cand = m.group(1)
        script = prompt + "\n" + cand + "\n" + test + f"\ncheck({entry})\nprint('m4ok')\n"
        try:
            r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                               timeout=15, input="")
            return 1.0 if "m4ok" in r.stdout else 0.0
        except subprocess.TimeoutExpired:
            return 0.0
    return f

def _check_mcq(letter):
    """GPQA-style letter answer: the LAST standalone A-D in the reply is what the model committed to."""
    def f(t):
        hits = re.findall(r"\b([A-D])\b", (t or "").upper())
        return 1.0 if hits and hits[-1] == letter.upper() else 0.0
    return f

def _check_bbh(target, kind):
    t = (target or "").strip().lower()
    if kind == "letter":
        want = re.sub(r"[()\s]", "", t)
        def f(text):
            hits = re.findall(r"\b([A-G])\b", (text or "").upper())
            return 1.0 if hits and hits[-1] == want.upper() else 0.0
        return f
    if kind == "int":
        want = _num(t)
        return lambda text: 1.0 if want is not None and _last_num(text) == want else 0.0
    if kind == "tf":
        def f(text):
            tt = (text or "").lower()
            a, b = tt.rfind("true"), tt.rfind("false")
            got = "true" if a > b else ("false" if b > a else "")
            return 1.0 if got == t else 0.0
        return f
    def f(text):                            # word: valid / invalid. "invalid" CONTAINS "valid", so
        tt = (text or "").lower()           # substring rfind is wrong (the round-2 self-test caught
        other = "valid" if t == "invalid" else "invalid"   # it grading its own gold 0.0); word-boundary
        a = [m.start() for m in re.finditer(r"\b" + t + r"\b", tt)]        # matches, last one decides
        b = [m.start() for m in re.finditer(r"\b" + other + r"\b", tt)]
        return 1.0 if (a and a[-1] > (b[-1] if b else -1)) else 0.0
    return f

def _stride(seq, n):
    """Fixed-stride sample: deterministic, spread over the whole file, no RNG to drift."""
    step = max(1, len(seq) // n)
    return seq[::step][:n]

def _lines(path):
    op = gzip.open if path.suffix == ".gz" else open
    with op(path, "rt", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]

def probes(bench_dir=None, force=False):
    d = Path(bench_dir) if bench_dir else ROOT / "Strata-data" / "bench"
    man = json.loads((d / "manifest.json").read_text())
    for name, meta in man.items():
        p = d / name
        if not p.exists():
            raise SystemExit(f"bench file missing: {p} - run tools/m4_bench_fetch.py")
        if not force and hashlib.sha256(p.read_bytes()).hexdigest() != meta["sha256"]:
            raise SystemExit(f"bench file changed since it was recorded: {name} "
                             f"(rerun tools/m4_bench_fetch.py --verify, or pass --force)")

    P = []
    gsm = _lines(d / "gsm_symbolic_test.jsonl")
    for row in _stride(gsm, 12):
        gold = row["answer"].split("####")[-1]
        P.append({"shape": "gsm_sym",
                  "prompt": row["question"] + "\nSolve it. Answer with just the number.",
                  "check": _check_gsm(gold), "gold": gold.strip()})
    math = _lines(d / "math500_test.jsonl")
    for row in _stride(math, 12):
        P.append({"shape": "math_hard",
                  "prompt": row["problem"] + "\nSolve it and put your final answer inside \\boxed{}.",
                  "check": _check_math(row["answer"]), "gold": row["answer"]})
    he = _lines(d / "humaneval.jsonl.gz")
    for row in _stride(he, 12):
        P.append({"shape": "code", "prompt": row["prompt"].rstrip() +
                  "\nComplete the function. Reply with only the Python code.",
                  "check": _check_humaneval(row["prompt"], row["test"], row["entry_point"]),
                  "gold": row["canonical_solution"], "exec_prompt": row["prompt"]})
    # GPQA diamond: the hardest open multiple choice (graduate science, expert-verified). Options
    # are shuffled with a FIXED RNG keyed to the row's index - placement bias is real in MCQ, and
    # a rerun must see the same letters too. Gold is a letter; scored as last A-D in the reply.
    if (d / "gpqa_diamond.csv").exists():
        with open(d / "gpqa_diamond.csv", newline="", encoding="utf-8") as f:
            gpqa = [r for r in csv.DictReader(f)
                    if (r.get("Question") or "").strip() and (r.get("Correct Answer") or "").strip()]
        for i, row in enumerate(_stride(gpqa, 12)):
            opts = [row["Correct Answer"], row["Incorrect Answer 1"],
                    row["Incorrect Answer 2"], row["Incorrect Answer 3"]]
            order = list(range(4))
            random.Random(4860938 + i).shuffle(order)
            opts = [opts[k] for k in order]
            gold = "ABCD"[opts.index(row["Correct Answer"])]
            P.append({"shape": "gpqa",
                      "prompt": (row["Question"].strip() + "\n\n"
                                 + "\n".join(f"{L}. {o.strip()}" for L, o in zip("ABCD", opts))
                                 + "\nAnswer with just the letter."),
                      "check": _check_mcq(gold), "gold": gold})
    for task, (shape, instr, kind) in BBH.items():
        rows = json.loads((d / f"bbh_{task}.json").read_text())
        for row in _stride(rows, 8):
            P.append({"shape": shape, "prompt": row["input"] + " " + instr,
                      "check": _check_bbh(row["target"], kind), "gold": row["target"]})
    return P


if __name__ == "__main__":
    # grader self-test against the REAL gold answers: every probe's checker must accept its own
    # gold (MATH via a boxed rendering, HumanEval via its canonical solution) - the discipline
    # that caught the round-1 Cy/Dee bug, applied to data instead of to my own expectations.
    P = probes()
    bad = 0
    for i, p in enumerate(P):
        if p["shape"] == "code":
            gold_text = p["exec_prompt"] + "\n" + p["gold"]
            ok = p["check"](gold_text)
        elif p["shape"] == "math_hard":
            ok = p["check"](f"reasoning...\n\\boxed{{{p['gold']}}}")
        else:
            ok = p["check"](p["gold"])
        if ok < 1.0:
            bad += 1
            print(f"  GOLD FAIL probe {i} [{p['shape']}]: gold {p['gold'][:40]!r} -> {ok}")
    shapes = sorted({p["shape"] for p in P})
    print(f"bank r2: {len(P)} probes over {len(shapes)} shapes: {shapes}")
    print("ALL GOLD ANSWERS GRADE 1.00" if not bad else f"{bad} GOLD FAILURES - fix before sweeping")
