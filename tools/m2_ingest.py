#!/usr/bin/env python3
"""tools/m2_ingest.py - import text or documents into the memory store as shelved facts.

WHOLE-DOC MODE IS THE DEFAULT (2026-10-17, the owner's redesign: "I don't want it to turn one
document into hundreds of little pieces"): the document is COPIED to --docs-dir (default
~/Documents), named by title + content hash, and ONE pointer fact is filed - title, size,
sha256, path, topics. The store keeps the address; the disk keeps the text; the model reads
the file when the subject matters. Re-ingesting an unchanged file dedups onto its own pointer
(the sha rides the text); a changed file hashes differently and refiles honestly.

FRAGMENT MODE (--mode fragments) is the original path, kept for knowledge-pack corpora where
sentence-level facts are the point (the lore-bible case). Its properties, recorded where they
bite:

  NO ENGINE CALLS. Sentences are stored VERBATIM with source=ingest-<date>. The extractor's
    over-inference disease ("asked for tips" -> "is learning a language", the township quiz ->
    "user likely lives in Marshall County") is structurally impossible here: the text says what
    it says, and the trust axis reads source=ingest-* as imported knowledge, not recalled chat.

  MECHANICAL SHELVING. Each sentence goes through the loop's map (tools/curate_loop.py,
    fact_class); --class shelves the WHOLE file and overrides the map - the lore-bible case:
    `m2_ingest.py krevvens.md --class FIC/KREVV` puts the whole setting on its cutter.

Idempotent: remember() dedups exact normalized text, so re-ingesting a file updates rather
than duplicates. Markdown-light: headings, code fences, table rows and bare labels are skipped.
FRAGMENT-GATED (2026-10-17, after the cleanup counted ~150 shards in the first SDK-guide run):
abbreviation splits are reunited before sentences are cut ("8 U.S.C." no longer dies mid
citation), and units that cannot stand alone - unbalanced parens, collapsed code, clauses that
start with a conjunction, sentences cut by a missed abbreviation, and bare adjective verdicts
under 45 chars with no data or determiner - are skipped, not stored. A rejected line costs one
sentence of a document; a stored shard pollutes every recall that passes it.

Usage:
  python3 tools/m2_ingest.py FILE... [--class SHELF] [--scope default] [--unit sentence|line]
      [--min 24] [--max 400] [--dry-run] [--url http://127.0.0.1:8005]
  "-" reads stdin.
"""
from __future__ import annotations

import argparse, datetime, hashlib, json, re, shutil, sys, urllib.request as U
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from curate_loop import fact_class                        # the map, the cutters, the margin rule

SENT_RE = re.compile(r"(?<=[.!?])\s+")

# The 2026-10-17 manual cleanup measured what sentence-splitting does to a real corpus (the
# SDK-guide ingest stored 1,755 rows, ~150 of them shards): the splitter cuts at "U.S." and
# "Stat." (citations die mid-parenthesis), indented code collapses to bare statements, and
# Q&A documents store answers whose questions the store never sees. The gates below are
# mechanical - no engine, no judgment - and conservative: a rejected unit costs one line of a
# document; a stored shard pollutes every recall that passes it.

ABBREV_TAIL = re.compile(r"(?:\b(?:stat|stats|u\.s|u\.s\.c|v|vs|dr|mr|mrs|ms|prof|inc|ltd|jr|sr|"
                         r"st|e\.g|i\.e|approx|fig|dept|gov|capt|ft|p\.m|a\.m)\.?|"
                         r"(?:\s+[A-Z]\.)+)\s*\.?$", re.I)

def reunite(parts: list[str]) -> list[str]:
    """Glue a fragment back onto its continuation when the split landed inside an abbreviation
    or a single-letter dotted name: 'The act (8 U.S.' + 'C. 1101) covers...' is ONE sentence."""
    out: list[str] = []
    for p in parts:
        if out and ABBREV_TAIL.search(out[-1].rstrip()):
            out[-1] = (out[-1].rstrip() + " " + p.lstrip()).strip()
        else:
            out.append(p)
    return out

CODE_HEAD = re.compile(r"^(?:import |from \S+ import|def |class |@|await |return |print\(|"
                       r"with |try |except |raise |yield |async |lambda |\w+\s*=\s*\S|\w+\.\w+\()")
FRAG_HEAD = re.compile(r"^(?:[,\)\]]|§|\d|\w+[)\]]|Q\d*[:?]|"
                       r"\band\b|\bbut\b|\bor\b|\bwhich\b|\bthat\b|\bbecause\b|"
                       r"\bso\b|\bto\b|\bfor\b|\bof\b|\bin\b|\bon\b|\bas\b|\bby\b)", re.I)
SHORT_INFO = re.compile(r"\d|\b(?:the|a|an|his|her|its|their|your|our)\b|['’]s\b")

def _balanced(s: str) -> bool:
    return (s.count("(") == s.count(")") and s.count("[") == s.count("]")
            and s.count("{") == s.count("}"))

def fragment(sent: str, unit: str) -> bool:
    """True when a unit cannot stand alone as a fact. Termination is enforced for sentence mode
    only (line mode opts into list items); an unterminated sentence unit after reunite() is a
    cut the abbreviation law missed."""
    s = sent.strip()
    if not _balanced(s) or CODE_HEAD.match(s) or "::" in s or "=>" in s:
        return True
    if s[:1].islower() or FRAG_HEAD.match(s):           # lowercase start = a clause cut mid-sentence
        return True
    if unit == "sentence" and not re.search(r"[.!?]$", s):
        return True
    if len(s) < 45 and not SHORT_INFO.search(s[1:]):   # a bare adjective verdict with no data,
        return True                                     # no determiner, no possessive: an answer
    return False                                        # whose question lives only in the document


def clean(line: str) -> str:
    """One markdown-ish line to a candidate sentence, or "" to skip it: headings, fences, tables,
    bullets and bare labels (lines ending in ':') carry structure, not facts."""
    s = line.strip()
    if not s or s.startswith(("#", "```", "|")) or re.match(r"^[-*+>] ", s) or s.endswith(":"):
        return ""
    s = s.replace("`", "").replace("**", "").replace("__", "").replace("*", "")
    return " ".join(s.split())


def strip_fences(text: str) -> str:
    """Drop fenced code blocks ENTIRELY, markers included: prose between the blocks is knowledge,
    the code inside them is syntax the store was never meant to recite (the first real-world doc
    test - a 22k-word SDK migration guide - made this bug obvious)."""
    out, in_fence = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def units(text: str, unit: str) -> list[str]:
    if unit == "line":
        return [c for c in (clean(l) for l in text.splitlines()) if c]
    out = []
    for para in re.split(r"\n[ \t]*\n", text):            # prose wraps across lines: rejoin the
        joined = " ".join(c for c in (clean(l) for l in para.splitlines()) if c)   # paragraph first
        if joined:
            out.extend(reunite(SENT_RE.split(joined)))   # abbreviation splits are glued back first
    return [s.strip() for s in out if s.strip()]


TOPIC_STOP = frozenset("""the and for that with this from they have been will your about into
other some more than when there their which what where while because should could would these
those then them here was were are you our out get just like also much many most even only over
under again each same very really thing things something anything nothing know think going want
need needs used using use upon per via among within without between first second every any all
""".split())


def doc_title(name: str, text: str) -> str:
    """The document's own first heading wins; else its first non-fence line; else the filename."""
    in_fence = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if s.startswith("#"):
            t = s.lstrip("#").strip(" *\t")
            if t:
                return t[:120]
        if s:
            return s[:120]
    return Path(name).stem


def doc_topics(text: str, k: int = 12) -> str:
    """Top frequency tokens minus the stop list - mechanical, no engine, and what makes the
    pointer findable by bm25 and cosine when the conversation turns to the document's subject."""
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9'-]{3,}", text)
             if w.lower() not in TOPIC_STOP]
    return ", ".join(w for w, _ in Counter(words).most_common(k))


def whole_doc(name: str, docs_dir: str) -> dict:
    """One document, one memory: the file is copied to docs_dir (content-named, collision-safe)
    and the pointer fact carries title, size, hash, path and topics. The store keeps the
    address; the disk keeps the text. Idempotent by content: the sha rides the pointer, so
    re-ingesting an unchanged file updates the pointer (exact-text dedup) instead of doubling
    it, and a changed file hashes differently and refiles honestly."""
    p = Path(name)
    raw = p.read_text(encoding="utf-8", errors="replace")
    sha = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]
    title = doc_title(name, raw)
    safe = re.sub(r"[^A-Za-z0-9 ._-]", "", title)[:60].strip() or p.stem
    docs = Path(docs_dir).resolve()
    dest = docs / f"{safe}-{sha[:8]}{p.suffix.lower() or '.txt'}"
    if p.resolve().parent == docs:
        dest = p.resolve()          # already filed where it belongs: point at the original,
                                    # no duplicate copy - the pointer IS the filing
    words = len(raw.split())
    topics = doc_topics(raw)
    pointer = (f"Document '{title}' ({p.suffix.lower() or '.txt'}, {words:,} words, sha256 {sha}) "
               f"is filed at {dest} (source: {p.resolve()}). Topics: {topics}. The store keeps this "
               f"pointer and the disk keeps the whole text: read {dest.name} when this subject "
               f"matters instead of answering from the pointer alone.")
    return {"pointer": pointer, "dest": str(dest), "sha": sha, "raw": raw, "words": words}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", help="text/markdown files to ingest ('-' = stdin)")
    ap.add_argument("--mode", choices=("whole", "fragments"), default="whole",
                    help="whole (default, 2026-10-17 redesign): copy the WHOLE document to "
                         "--docs-dir and file ONE pointer fact - the store keeps the address, "
                         "the disk keeps the text, and one document stops becoming hundreds of "
                         "shards. fragments: the pre-2026-10-17 sentence-splitting path")
    ap.add_argument("--docs-dir", default=str(Path.home() / "Documents"),
                    help="where whole documents are filed (default ~/Documents)")
    ap.add_argument("--class", dest="klass", default="", help="shelf for the whole file, e.g. "
                                                              "FIC/KREVV (overrides the map)")
    ap.add_argument("--scope", default="default")
    ap.add_argument("--unit", choices=("sentence", "line"), default="sentence")
    ap.add_argument("--min", type=int, default=24, help="shortest ingestable sentence (fragments "
                                                        "join noise, not knowledge)")
    ap.add_argument("--max", type=int, default=400, help="longest (atomic facts retrieve better; "
                                                         "the store itself caps at 4000 chars)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-gate", action="store_true", help="store every unit that passes min/max - "
                    "the pre-2026-10-17 behavior - for curated documents whose fragments you want "
                    "kept verbatim anyway")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    a = ap.parse_args()

    src = f"ingest-{datetime.date.today().isoformat()}"
    stored = deduped = skipped = evicted = 0
    by_class: Counter = Counter()
    if a.mode == "whole":
        # one document, one memory: copy to --docs-dir, file ONE pointer fact. No sentence
        # splitting, no fragment gate - the whole text lives on disk, the store keeps the address.
        for name in a.files:
            if name == "-":
                raise SystemExit("whole mode needs files on disk (the copy is the point); "
                                 "use --mode fragments for stdin")
            info = whole_doc(name, a.docs_dir)
            klass = a.klass or fact_class(info["pointer"], a.scope)
            by_class[klass or "wild"] += 1
            if a.dry_run:
                print(f"  would file {info['dest']}\n  would store: {info['pointer'][:140]}...")
                stored += 1
                continue
            if Path(name).resolve() != Path(info["dest"]).resolve():
                shutil.copyfile(name, info["dest"])     # in-place files need no copy
            body = {"op": "remember", "text": info["pointer"], "scope": a.scope, "source": src,
                    "provenance": {"file": Path(name).name, "sha256": info["sha"],
                                   "path": info["dest"]}}
            if klass:
                body["class"] = klass
            req = U.Request(a.url.rstrip("/") + "/memory", method="POST",
                            data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            r = json.loads(U.urlopen(req, timeout=30).read())
            if r.get("deduped"):
                deduped += 1
            else:
                stored += 1
            print(f"  filed {info['dest']} -> pointer id {r.get('id')}"
                  f"{' (deduped: unchanged content)' if r.get('deduped') else ''}")
        print(f"{a.mode}: pointers {stored}, deduped {deduped}; shelves: {dict(by_class)}")
        return
    for name, text in [(f, (sys.stdin.read() if f == "-" else
                            strip_fences(Path(f).read_text(encoding="utf-8", errors="replace"))))
                       for f in a.files]:
        for n, sent in enumerate(units(text, a.unit), 1):
            if not (a.min <= len(sent) <= a.max) or (not a.no_gate and fragment(sent, a.unit)):
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
            evicted += len(r.get("evicted") or [])   # the budget line spoke: name what it cost
    what = "would store" if a.dry_run else "stored"
    print(f"{what} {stored}, deduped {deduped}, skipped {skipped} (out of size bounds), "
          f"budget-evicted {evicted}; shelves: {dict(by_class)}")
    if a.dry_run:
        print("dry-run: pass without --dry-run to post through the API")


if __name__ == "__main__":
    main()
