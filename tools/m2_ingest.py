#!/usr/bin/env python3
"""tools/m2_ingest.py - import text or documents into the memory store as shelved facts.

The knowledge-pack path for the LCC system (the user's framing: shelves are how the model's
knowledge grows without retraining or LoRA - vetted data, addressed by call number, pulled by
the conversations that need it). Two properties make this different from the curation loop,
on purpose:

  NO ENGINE CALLS. Sentences are stored VERBATIM with source=ingest-<date>. The extractor's
    over-inference disease ("asked for tips" -> "is learning a language", the township quiz ->
    "user likely lives in Marshall County") is structurally impossible here: the text says what
    it says, and the trust axis reads source=ingest-* as imported knowledge, not recalled chat.

  MECHANICAL SHELVING. Each sentence goes through the loop's map (tools/curate_loop.py,
    fact_class); --class shelves the WHOLE file and overrides the map - the lore-bible case:
    `m2_ingest.py krevvens.md --class FIC/KREVV` puts the whole setting on its cutter.

Idempotent: remember() dedups exact normalized text, so re-ingesting a file updates rather
than duplicates. Markdown-light: headings, code fences, table rows and bare labels are skipped.

Usage:
  python3 tools/m2_ingest.py FILE... [--class SHELF] [--scope default] [--unit sentence|line]
      [--min 24] [--max 400] [--dry-run] [--url http://127.0.0.1:8005]
  "-" reads stdin.
"""
from __future__ import annotations

import argparse, datetime, json, re, sys, urllib.request as U
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from curate_loop import fact_class                        # the map, the cutters, the margin rule

SENT_RE = re.compile(r"(?<=[.!?])\s+")


def clean(line: str) -> str:
    """One markdown-ish line to a candidate sentence, or "" to skip it: headings, fences, tables,
    bullets and bare labels (lines ending in ':') carry structure, not facts."""
    s = line.strip()
    if not s or s.startswith(("#", "```", "|")) or re.match(r"^[-*+>] ", s) or s.endswith(":"):
        return ""
    s = s.replace("`", "").replace("**", "").replace("__", "").replace("*", "")
    return " ".join(s.split())


def units(text: str, unit: str) -> list[str]:
    if unit == "line":
        return [c for c in (clean(l) for l in text.splitlines()) if c]
    out = []
    for para in re.split(r"\n[ \t]*\n", text):            # prose wraps across lines: rejoin the
        joined = " ".join(c for c in (clean(l) for l in para.splitlines()) if c)   # paragraph first
        if joined:
            out.extend(SENT_RE.split(joined))             # then split sentences
    return [s.strip() for s in out if s.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", help="text/markdown files to ingest ('-' = stdin)")
    ap.add_argument("--class", dest="klass", default="", help="shelf for the whole file, e.g. "
                                                              "FIC/KREVV (overrides the map)")
    ap.add_argument("--scope", default="default")
    ap.add_argument("--unit", choices=("sentence", "line"), default="sentence")
    ap.add_argument("--min", type=int, default=24, help="shortest ingestable sentence (fragments "
                                                        "join noise, not knowledge)")
    ap.add_argument("--max", type=int, default=400, help="longest (atomic facts retrieve better; "
                                                         "the store itself caps at 4000 chars)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    a = ap.parse_args()

    src = f"ingest-{datetime.date.today().isoformat()}"
    stored = deduped = skipped = 0
    by_class: Counter = Counter()
    for name, text in [(f, (sys.stdin.read() if f == "-" else Path(f).read_text(encoding="utf-8",
                                                                                  errors="replace")))
                       for f in a.files]:
        for n, sent in enumerate(units(text, a.unit), 1):
            if not (a.min <= len(sent) <= a.max):
                skipped += 1
                continue
            klass = a.klass or fact_class(sent, a.scope)
            by_class[klass or "wild"] += 1
            if a.dry_run:
                stored += 1                                 # "would store" counts too
                continue
            body = {"op": "remember", "text": sent, "scope": a.scope, "source": src,
                    "provenance": {"file": Path(name).name, "n": n}}
            if klass:
                body["class"] = klass
            req = U.Request(a.url.rstrip("/") + "/memory", method="POST",
                            data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            r = json.loads(U.urlopen(req, timeout=30).read())
            if r.get("deduped"):
                deduped += 1
            else:
                stored += 1
    what = "would store" if a.dry_run else "stored"
    print(f"{what} {stored}, deduped {deduped}, skipped {skipped} "
          f"(out of size bounds); shelves: {dict(by_class)}")
    if a.dry_run:
        print("dry-run: pass without --dry-run to post through the API")


if __name__ == "__main__":
    main()
