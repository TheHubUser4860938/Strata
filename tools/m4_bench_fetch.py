#!/usr/bin/env python3
"""m4_bench_fetch - pull the round-2 benchmark bank for m4_reason, once, verifiably.

Round 1's hand-written probes proved the instrument works and the model saturates it (six of
seven shapes at 1.00 for every arm). Round 2 therefore stands on published benchmarks chosen for
exactly the two properties the fork's measurement law needs: DETERMINISTIC scoring (exact-answer
match or exec'd unit tests - no LLM judge anywhere) and published headroom on this class of model
(nothing here is at 1.00 for frontier smalls, so saturation cannot hide a mode effect).

What gets fetched into --dir (default Strata-data/bench, git-ignored and OUTSIDE /tmp - the
LongMemEval corpus got eaten by a /tmp cleanup once and the venue work lost a day to it):

  gsm_symbolic_test.jsonl  apple/GSM-Symbolic main (v1), ICLR 2025 - GSM8K problems regenerated
                           from templates with fresh names and numbers: the anti-memorization
                           math shape. Models that aces'd GSM8K lost 7-15 points here; that gap
                           is exactly the headroom round 1 lacked. JSONL, exact-number scoring.
  math500_test.jsonl       HuggingFaceH4/MATH-500 - the canonical 500-problem subset of the
                           Hendrycks MATH set (levels 1-5, competition math). JSONL with a gold
                           "answer" field; \boxed{} scoring, strict.
  humaneval.jsonl.gz       openai/human-eval (MIT) - 164 hand-written function completions with
                           the canonical unit tests in the file. Scored by EXECUTION in a fresh
                           subprocess: pass/fail, no regex, no judge.
  bbh_<task>.json          BIG-Bench Hard (Apache-2.0) via the HF datasets-server JSON rows (the
                           parquet originals need pyarrow this venv does not have). Five tasks,
                           every one scored by exact match on a short target: boolean_expressions
                           (True/False), multistep_arithmetic_two (integer), date_understanding
                           (a letter, the known weak spot), logical_deduction_seven_objects (a
                           letter), formal_fallacies (valid/invalid). BBH is the set where "let's
                           think step by step" was discovered - the mode-effect question in its
                           original habitat.

GPQA (graduate science MCQ, the hardest open multiple choice) joined 2026-10-17: the origin is
gated (the owner's HF account was granted access) and CC BY-NC 4.0 - internal measurement, the CSV
never enters git. The access token lives in .hf_token next to the data (chmod 600); it is NEVER
stored in the memory store, where it would sit in plaintext and ride back into every recall.
Every file lands with a sha256 in manifest.json: a rerun that reads different bytes says so.

    python3 tools/m4_bench_fetch.py            # fetch what is missing, verify what is present
    python3 tools/m4_bench_fetch.py --verify   # re-hash only, no network
"""
from __future__ import annotations

import argparse, gzip, hashlib, json, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# task -> (HF config name, rows to keep) for the BIG-Bench Hard pulls
BBH_TASKS = ["boolean_expressions", "multistep_arithmetic_two", "date_understanding",
             "logical_deduction_seven_objects", "formal_fallacies"]

FILES = {
    "gsm_symbolic_test.jsonl": ("https://huggingface.co/datasets/apple/GSM-Symbolic/resolve/"
                                "main/main/test.jsonl", "CC BY 4.0 (Apple, GSM-Symbolic v1)"),
    "math500_test.jsonl":      ("https://huggingface.co/datasets/HuggingFaceH4/MATH-500/resolve/"
                                "main/test.jsonl", "MIT (derived from Hendrycks MATH)"),
    "humaneval.jsonl.gz":      ("https://raw.githubusercontent.com/openai/human-eval/master/"
                                "data/HumanEval.jsonl.gz", "MIT (openai/human-eval)"),
    # GPQA diamond: gated at the origin (the owner's HF account was granted access 2026-10-17) and
    # CC BY-NC 4.0 - internal measurement only, the CSV itself never enters git. The token lives in
    # .hf_token (chmod 600, inside the git-ignored data dir) and is NEVER stored in the memory
    # store, where it would sit in plaintext and ride back into recall.
    "gpqa_diamond.csv":        ("https://huggingface.co/datasets/Idavidrein/gpqa/resolve/"
                                "main/gpqa_diamond.csv", "CC BY-NC 4.0 (Idavidrein/GPQA, gated)"),
}


def get(url, binary=False, timeout=300, token=None):
    headers = {"User-Agent": "m4_bench_fetch/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bbh_rows(task: str, n=100) -> list:
    """BIG-Bench Hard rows as JSON straight from the datasets-server (server-side parquet->JSON)."""
    out = []
    for off in range(0, n, 100):
        u = ("https://datasets-server.huggingface.co/rows?dataset=SaylorTwift%2Fbbh&config="
             + task + "&split=test&offset=" + str(off) + "&length=" + str(min(100, n - off)))
        out.extend(r["row"] for r in json.loads(get(u))["rows"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=str(ROOT / "Strata-data" / "bench"))
    ap.add_argument("--verify", action="store_true", help="re-hash existing files, no network")
    a = ap.parse_args()
    d = Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    man_path = d / "manifest.json"
    man = json.loads(man_path.read_text()) if man_path.exists() else {}

    for name, (url, lic) in FILES.items():
        p = d / name
        if a.verify or (p.exists() and name in man):
            print(f"  {name}: {'re-hash' if a.verify else 'present'} -> {sha256(p)[:16]}")
            continue
        tok_file = d / ".hf_token"
        tok = tok_file.read_text().strip() if tok_file.exists() else None
        print(f"  fetching {name}{' (gated, token file)' if 'gpqa' in name and tok else ''} ...")
        p.write_bytes(get(url, binary=True, token=tok if "huggingface.co/datasets/Idavidrein" in url else None))
        man[name] = {"url": url, "license": lic, "sha256": sha256(p), "bytes": p.stat().st_size}
        print(f"  {name}: {man[name]['bytes']:,} bytes, sha {man[name]['sha256'][:16]}")

    for task in BBH_TASKS:
        name = f"bbh_{task}.json"
        p = d / name
        if a.verify or (p.exists() and name in man):
            print(f"  {name}: {'re-hash' if a.verify else 'present'} -> {sha256(p)[:16]}")
            continue
        rows = bbh_rows(task)
        assert rows and "input" in rows[0] and "target" in rows[0], f"bad {task} rows"
        p.write_text(json.dumps(rows))
        man[name] = {"url": f"datasets-server rows SaylorTwift/bbh {task} test",
                     "license": "Apache-2.0 (BIG-Bench)", "sha256": sha256(p),
                     "bytes": p.stat().st_size, "rows": len(rows)}
        print(f"  {name}: {len(rows)} rows, sha {man[name]['sha256'][:16]}")

    # sanity-read every file so a corrupt download is caught HERE, not mid-sweep
    n_lines = lambda p: sum(1 for ln in gzip.open(p, "rt") if ln.strip()) if p.suffix == ".gz" \
        else sum(1 for ln in p.open() if ln.strip())
    for name in list(FILES) + [f"bbh_{t}.json" for t in BBH_TASKS]:
        p = d / name
        if name.startswith("bbh_"):
            rows = json.loads(p.read_text())
            print(f"  read {name}: {len(rows)} rows, first target {rows[0]['target'][:20]!r}")
        elif name.endswith(".csv"):
            import csv
            with open(p, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            assert rows and "Question" in rows[0] and "Correct Answer" in rows[0], "bad gpqa csv"
            print(f"  read {name}: {len(rows)} questions")
        else:
            print(f"  read {name}: {n_lines(p)} lines")
    man_path.write_text(json.dumps(man, indent=1))
    print(f"manifest: {man_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
