#!/usr/bin/env python3
"""tools/curate_loop.py - T5-M1 spike: the curation loop over the journal backlog (plan §11).

What is missing today, measured 2026-10-11: the store journals every conversation turn but
nothing processes them - 429 of 429 journaled turns PENDING (GET /memory stats). This spike IS
the missing loop, end to end:

  drain turns_pending (POST /memory {"op":"turns"})
  -> EXTRACT question-shaped candidates (one muted generation per turn: FACT || PROBE || GOLD
     || SCOPE lines; the self-probe step the scorer forces - a free-text fact is only
     scoreable once the model can state a question only that fact answers)
  -> COLD-SCORE every candidate: 3 passes, probe asked muted on the same channel discipline
     the C-series banked (reasoning off, temperature 0.8, X-Strata-Memory: off, geometry
     usage.prompt_tokens == local render else that ask is VOID for that candidate)
  -> FOUR-WAY novelty sort (plan §11):
       FILL        0/3 gold, answers idk           -> weights cannot reach it: STORE
       STABILIZE   1-2/3 gold                      -> weak/unstable prior: STORE
       CORRECT     0/3 gold, all 3 CONFABULATE     -> memory must OVERRIDE a wrong prior:
                                                     STORE flagged (priority injection later)
       DONT-STORE  3/3 gold                        -> weights already own it: log only
     (0/3 with a MIX of idk and confabulation stays FILL with a mixed flag - the confab-vs-idk
     line is the claim, a half-shimmer answer does not support it)
  -> remember(source="curation-<date>", provenance={turn_id, probe, gold, cold answers, cls})
     via POST /memory - the review list is then just "list by source tag"
  -> mark turns processed (direct SQLite: WAL + busy_timeout 5000 per serve/memory.py:114-118,
     cross-process writes are safe; the product form moves this to a server-side job with a
     "mark" op)
  -> print the review list grouped by class.

Modes: default = one-shot drain of everything pending (--limit caps turns this run).
--sweep = the hot-loop shape: batches gated on SUSTAINED engine quiet (activity.last_request_at
aged --idle-window seconds, default 240) so a human working the port never loses a checkpoint
to a curation ask; --interval seconds between checks. --extract-only stops after
extraction (smoke the extractor before burning cold-score asks). --dry-run renders and counts
asks, touches no engine.

Honest-cost note: the backlog drain is ~1 extraction ask per turn + 3 cold asks per candidate;
429 turns at ~2 candidates/turn is on the order of 3,000 asks ~ 45-70 min of idle engine time,
run once. The daily steady state is ~30-50 turns ~ 2-4 min spread over idle gaps.

State: tools/state/curate_<tag>.jsonl + review_<tag>.txt carry USER-DERIVED TEXT (facts, probes,
answers) - deliberately UNTRACKED by git (personal-hygiene policy); the evidence that lands in
git is the aggregate counts + the verdict block, never raw personal text.
"""
from __future__ import annotations
import argparse, collections, datetime, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request as U
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "spikes" / "window"))
sys.path.insert(0, str(ROOT / "spikes" / "d2_dissect"))
from venue_render import load_tokenizer, load_template, live_render   # server-exact render
from gate1_driver import hit, norm                                    # the strict scorer

COLD_PREAMBLE = ("Answer the question with the shortest exact answer - a name, number, date, "
                 "or short phrase. If you do not know, answer exactly: idk. No guessing, no "
                 "sentences, no explanation.\n\nQuestion:")
_EXTRACT_HEAD = (
    "You are scanning one work conversation for durable facts worth remembering. List up to "
    "{k} facts that a fresh assistant session could NOT already know: personal details about "
    "the user, or project-specific findings and decisions. Skip generic chatter, small talk, "
    "tool noise, and anything answerable from general knowledge. For each fact output ONE "
    "line exactly in this format:\n"
    "FACT: <one sentence> || PROBE: <a question only this fact answers> || GOLD: <the "
    "shortest exact answer to PROBE> || SCOPE: user|project\n"
    "No other lines, no numbering, no commentary.\n")
_DATES_RULE = (
    "DATES: this conversation happened on {observed}. Resolve every relative date against that "
    "day and write the ABSOLUTE date (YYYY-MM-DD) into the FACT. 'yesterday', 'last week' and "
    "'on Friday' are unreadable to a session that arrives months later, and the memory block "
    "cannot order them for the reader either: a rendered note line is `[n] (scope) text` with no "
    "date in it at all, so a fact that does not name its own day is unorderable even when it is "
    "present. Never invent a date: if the day is unknown, leave it out.\n")
_EXTRACT_TAIL = "\nCONVERSATION:\nuser: {user}\nassistant: {assistant}"
EXTRACT_PROMPT = _EXTRACT_HEAD + _DATES_RULE + _EXTRACT_TAIL       # the shipped shape
EXTRACT_PROMPT_UNDATED = _EXTRACT_HEAD + _EXTRACT_TAIL             # control arm: byte-identical to
# the prompt every fact now in the store was written with (sha256 e7c8fa6b64114ee0, pinned by
# tools/test_curate_grounding.py so the control cannot drift into a second variable)
#
# Why the date is in the prompt at all: the venue record's worst question type is temporal
# reasoning at 0.465, and the diagnostic (spikes/lme/TEMPORAL_DIAG.md) names the mechanism as
# multi-event ORDERING - "an ordering question needs three dated events and lexical recall proposes
# the one that shares the question's rare words". The journal has stored created_at for every turn
# and pending_turns() has always returned it (serve/memory.py:1243); the extract prompt was the one
# place it went missing, so a turn saying "last week we decided" got written down dateless by
# design. This costs no extra ask and no retrieval compute - it lengthens one prompt.


def observed_on(ts) -> str:
    """A turn's timestamp in the form a reader can reason with: date plus weekday, local time (the
    weekday is what makes "Friday" resolvable). A missing stamp says so rather than guessing."""
    try:
        if not ts:
            raise ValueError("no stamp")
        return time.strftime("%Y-%m-%d %A", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return "an unknown date (write no date rather than a guessed one)"


def extract_prompt(t: dict, k: int, dated: bool = True) -> str:
    """The extract ask for ONE journaled turn. `dated=False` reproduces the pre-grounding prompt
    byte-for-byte, which is what makes the A/B a single-variable test instead of a rewrite plus a
    hope."""
    tpl = EXTRACT_PROMPT if dated else EXTRACT_PROMPT_UNDATED
    return tpl.format(k=k, user=scrub(t["user"])[:1200], assistant=scrub(t["assistant"])[:1600],
                      observed=observed_on(t.get("created_at")))
# the idk set mirrors gate1_driver.hit()'s idk guard (spikes/window/gate1_driver.py) - the
# SAME predicate that zeroes a cold answer must decide confab-vs-idk, or the two disagree.
IDK = ("idk", "i don't know", "i do not know", "unknown", "don't know", "no answer", "")


def http(url, payload=None, headers=None, timeout=960):
    data = json.dumps(payload).encode() if payload is not None else None
    req = U.Request(url, data=data, method="POST" if data else "GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with U.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {"raw": "<unreadable error body>"}
    except Exception as e:                                  # transport: recorded, not crashed
        return 0, {"error": {"message": f"transport: {type(e).__name__}: {e}"[:160]}}


def idk_p(answer: str) -> bool:
    return norm(answer) in IDK


# Chat-template control tokens inside JOURNALED text (2026-10-11 drain, turn 27: the user
# intentionally typed template fragments into the webapp, and the turn was journaled verbatim)
# break live_render's measurement assert - venue_render.py:47 - and would poison any re-render
# of this very log. The assert checks the GROUND TRUTH set (serve/frontend.py:250-257: every
# tokenizer control-token literal + LITERAL_TAGS, longest-first like the tokenizer matches), so
# scrub() replaces exactly that set - filled from the LIVE tokenizer in main(), no guessed
# spellings - plus a regex for partial fragments. Placeholders keep the words around them.
CTRL = re.compile(r"<\|[^|>\n]{1,40}\|?>?")
CTRL_LITERALS: list[str] = []


def scrub(s: str) -> str:
    for lit in CTRL_LITERALS:
        s = s.replace(lit, "<ctrl>")
    s = CTRL.sub("<ctrl>", s or "")
    # Final belt for EVERY downstream stream (2026-10-11, the user's rule): space-break the
    # bare pipe combos so the delimiter can never be reassembled intact by anything that
    # reads this text - the serving template treats the sequence as end-of-turn, and any
    # stream that reproduces it (assistant included, several times tonight) dies mid-sentence.
    return s.replace("<|", "< |").replace("|>", "| >")


def classify(cold: list[dict]) -> tuple[str, bool]:
    """cold = surviving (geometry-OK) scored passes [{"gold_hit":b,"idk":b},...].
    Returns (class, mixed_flag). CORRECT requires ALL passes confidently wrong - the claim is
    'the model asserts a wrong answer', a half-shimmer run does not support it (plan §11)."""
    hits = sum(1 for c in cold if c["gold_hit"])
    if hits == len(cold):
        return "DONT-STORE", False
    if hits >= 1:
        return "STABILIZE", False
    confab = [c for c in cold if not c["idk"]]
    if len(confab) == len(cold):
        return "CORRECT", False
    return "FILL", bool(confab)                             # 0/3 with any confab = mixed


def _norm(text):
    """Same identity the store dedups on (serve.memory.norm_text), copied so the loop stays a
    standalone script: casefolded, punctuation to spaces, whitespace collapsed."""
    return " ".join("".join(c if c.isalnum() or c.isspace() else " " for c in str(text).casefold()).split())


# words that never carry a fact's payload, whatever the corpus says - copied from serve/memory.py
# (same standalone-script reason as _norm above): without this, a small fetched store makes "the"
# look rare (df 1) and two unrelated sentences "share" it; the live store hides the bug only
# because df(the) is always huge. memory.py's _dedupe and tools/m2_merge both exclude STOP; now
# all three content addresses agree.
STOP = frozenset(("a an and are as at be by can could did do does for from had has have he her his how i if in "
                  "is it its me my no not of on or our she so than that the their them then there these they this "
                  "to us we what when where which who whom whose why will with would you your").split())


def _payload(text, df):
    """A fact's content address: only its rare tokens (STOP words out, document frequency <= 3).
    The boilerplate of a template-shaped corpus carries no information; the payload is what makes
    the fact itself."""
    return frozenset(t for t in set(_norm(text).split()) if t not in STOP and df.get(t, 0) <= 3)


def _probe_shape(user, assistant):
    """True when a turn looks like the venue's own quiz traffic: a question in, an answer of <= 12
    words out (the user's quiz format: entity name or idk). Probe answers are the model hearing the
    gold sheet - they must not mint corrections or personal state-claims (measured 2026-10-12: the
    sweep digested think-token experiment turns and let one quiz answer SUPERSEDE another airport's
    fact). Cheap text shape, deliberately: it gates only the CORRECT route, nothing else."""
    u = str(user or "").strip()
    a = str(assistant or "").strip()
    return u.endswith("?") and 0 < len(a.split()) <= 12


def _pick_target(fact, rows, min_created=0.0):
    """Pure matching (unit-tested offline in tools/test_curate_supersede.py): rows are
    (id, text, hits, created_at) tuples of active facts; returns (id, shared, ambiguous_ids or
    None). Beyond the payload-intersection rule, two guards added after the measured 2026-10-12
    Westhope->Pruetz->Arthur cascade:
    SUBJECT: the shared tokens must include one with df <= 1 in the fetched store - the target's
    own unique subject. Corpus connectives lie about rarity in small sub-corpora: faa/location/
    identifier/municipal sat at df 3-6 in the airport rows and made every airport row "match"
    every other, so a Pruetz correction ate a Westhope row 40 seconds old.
    SIBLINGS: rows created at or after min_created belong to THIS batch, not to the prior truth -
    candidates from one sweep must not supersede each other's fresh rows."""
    rows = [r for r in rows if not min_created or float(r[3] or 0) < min_created]
    df = collections.Counter(t for _, txt, _, _ in rows for t in set(_norm(txt).split()))
    pay = _payload(fact, df)
    if not pay:                                               # all-boilerplate: too weak to target anything
        return None, 0, None
    want = _norm(fact)
    # shared rare tokens, not whole-payload equality: a rephrase shares the FULL payload, a true
    # contradiction ("KCVN is licensed to Frisco" vs the Cozad row) shares the SUBJECT tokens -
    # both are the fact this one replaces; identical text is dedup's job, not supersede's. A lone
    # shared token only targets when it dominates the smaller payload (>=0.5): one incidental
    # rare-word coincidence ("broadcasts") must not retire an unrelated fact - and the whole
    # match must be anchored by a token unique to the target (the SUBJECT rule above).
    def ok(pay2):
        inter = pay & pay2
        return ((len(inter) >= 2 or (len(inter) >= 1 and len(inter) >= 0.5 * min(len(pay), len(pay2))))
                and any(df.get(t, 0) <= 1 for t in inter))
    scored = sorted(((len(pay & _payload(txt, df)), h, i) for i, txt, h, _ca in rows
                     if _norm(txt) != want and ok(_payload(txt, df))), reverse=True)
    if not scored:
        return None, 0, None
    if len(scored) > 1 and scored[0][:2] == scored[1][:2]:    # two rows share the subject equally:
        return None, scored[0][0], [s[2] for s in scored[:4]]  # ambiguous: store as a new fact
    return scored[0][2], scored[0][0], None


def supersede_id(url, fact, scope, log, min_created=0.0):
    """The id of the active fact this CORRECT-class candidate replaces, or None (see _pick_target
    for the matching rules). Prefers the row with the most hits - the one recall actually serves,
    so the fix lands where the wrong fact was winning. None on any lookup trouble: a correction
    that cannot find its target still stores as a plain new fact (op=remember), never fails."""
    try:
        st, r = http(f"{url}/memory?scope={urllib.parse.quote(scope)}&limit=500")
        rows = [(m["id"], m["text"], m.get("hits", 0), m.get("created_at", 0))
                for m in (r or {}).get("memories", [])] if st == 200 else []
    except Exception as e:                                    # noqa: BLE001 - a lookup is never worth losing a fact
        log({"kind": "supersede_lookup", "err": str(e)[:100]})
        return None
    if not rows:
        return None
    old, shared, amb = _pick_target(fact, rows, min_created)
    if old is None and shared:
        log({"kind": "supersede_ambiguous", "shared": shared, "ids": amb})
    elif old is not None:
        log({"kind": "supersede", "old_id": old, "shared": shared})
    return old


# Top-class keyword map for the v1 shelf mark (PHASE 4.5). Deliberately small and distinctive:
# these are the tokens that DECIDE a shelf, not the ones that decorate a sentence. "token" and
# "model" appear everywhere; "generate.cpp" and "vram" do not.
CLASS_MAP = {
    "STR": {"engine", "generate.cpp", "prefill", "decode", "cuda", "hip", "sycl", "nvcc", "mtp",
            "drafter", "drafts", "draft", "speculative", "quantized", "quant", "gguf", "ggml",
            "yarn", "rope", "k8v4", "pcie", "overlap", "checkpoints", "checkpoint", "llama.cpp",
            "flash-next", "qwen3.8", "iq3_s", "unloaded", "unload", "slot", "slots", "context"},
    "MEM": {"memory", "memories", "recall", "injection", "injected", "journal", "journaled",
            "journaling", "curation", "curation's", "curated", "curate", "sweep", "swept",
            "cartridge", "cartridges", "mam", "fts", "invalidate", "invalidation", "supersede",
            "supersede_id", "supersedes", "superseded", "budget", "store", "facts", "fact",
            "dedup", "dedups", "deduplicated", "merge", "m2_merge", "provenance", "scopes", "scope"},
    "HW": {"gpu", "vram", "rtx", "4090", "5070", "nvidia", "amd", "radeon", "epyc", "ryzen",
           "x3d", "ddr", "ddr5", "ram", "cpu", "ssd", "nvme", "motherboard", "cooler", "psu",
           "passkey", "yubikey", "desktop", "plugged"},   # the user's own security hardware (scan 2026-10-12)
    "EVAL": {"township", "townships", "county", "counties", "licensed", "broadcast", "station",
             "stations", "minnesota", "nebraska", "quiz", "gold", "probe", "probes", "canary",
             "canaries", "zylophwick", "greemak", "corpus", "bench", "benchmark", "venue",
             "venues", "gate", "f1", "recall_limit",
             # 2026-10-12 wild-scan: the FAA-airport arm of the same quiz corpus sat homeless
             # (105+ rows) because the map knew stations and townships but not airports -
             # class=EVAL was silently MISSING a third of the corpus it claimed to filter.
             "airport", "airports", "faa", "municipal", "identifier", "field", "assigned",
             "aviation", "trivia"},
    # 2026-10-12 taxonomy v1.1 - grown from the store, not invented: a content scan found 12
    # fiction-canon rows wild, ~30 industry rows homeless and 7 decision records with no shelf.
    # An empty shelf is a promise retrieval cannot keep; a shelf the store already fills is one.
    "FIC": {"krevven", "krevvens", "wendfallen", "voldranium", "opuvvi", "opuvvian", "vasketh",
            "thule", "thyravelle", "gerudo", "zephyrine", "quorindine", "aurelith", "dusthenom",
            "conjurin", "conjurins", "cuisine", "dish", "dishes", "canon", "fictional", "archive",
            "custodian", "keeper", "bell", "tide", "expanse"},
    # NB nvidia/amd/gpu stay OUT of TECH: they are the tokens of MY machine's requirements (HW);
    # industry facts arrive with their own names (intel, dlss, epyc, arc) - sharing the generic
    # words would tie every requirement row against every news row and the margin would go wild.
    "TECH": {"intel", "arc", "b390", "panther", "wildcat", "dlss", "epyc", "9006", "geforce",
             "blackwell", "rdna", "gddr", "2k27", "wembanyama", "caitlin", "nba", "laptop",
             "laptops", "introduces", "announced"},
    "DEC": {"decided", "decision", "decisions", "approved", "greenlit", "chose", "opted",
            "vetoed", "rejected", "overruled"},
    # 2026-10-12 evening: the first user knowledge packs arrived (POL/ARSA 601 facts, POL 347,
    # AI 139) - the map learns the vocabulary of a shelf only AFTER the shelf exists.
    "POL": {"congress", "senate", "bill", "amendment", "amendments", "constitution",
            "constitutional", "legislation", "legislative", "tariff", "tariffs", "immigration",
            "immigrant", "citizenship", "sovereignty", "sovereign", "republic", "founding",
            "federal", "repeal", "repatriation", "deportation", "birthright", "emergency",
            "statute", "ratify", "ratification", "arsa", "manifest"},
    "AI": {"agent", "agents", "autonomous", "evolution", "odysseus", "mcp", "embedding",
           "embeddings", "rag", "orchestration", "orchestrator", "multimodal"},
    "OPS": {"setup", "install", "installation", "dev-up", "dev-up.sh", "dev-down", "dev-down.sh",
            "config", "venv", "cron", "crontab", "systemd", "timer", "git", "commit", "commits",
            "push", "branch", "rebase", "release", "build", "compiled", "compiler", "snapper",
            "reboot", "rebooted", "restart", "restarted"},  # server lifecycle (scan 2026-10-12)
    # NB "user" is deliberately NOT a USR keyword: every journaled fact reads "The user ..." -
    # it would score 1 everywhere, quietly stealing the margin from real shelves. Scope already
    # says a fact is the user's; the USR SHELF is for facts that are personal by content. Same
    # reason "name" stays out: "the name of the township" is a quiz question, not a name fact.
    "USR": {"prefers", "preference", "preferences", "personal", "privately", "adult",
            "family", "middle", "username", "daily", "likes", "loves", "hates",
            "wants", "hobbies", "birthday", "daughter", "son", "wife", "husband", "learning",
            "language", "lives", "friend", "kids", "email", "personalization", "goal"},
            # "home" stays out too: /home/<name>/ appears in every project path - paths are not
            # the personal lane
}

# A person's own name is the strongest USR signal there is ("<name> prefers X" is personal whatever
# the words), but a real name does not belong in git - the store holds it, the repo must not print
# it.  So the machine supplies it: STRATA_USER_NAME (the systemd sweep unit sets it; a manual run
# without it degrades gracefully, the other hints still classify).  Names split on spaces and commas.
USR_NAMES = {w for w in re.split(r"[,\s]+", os.environ.get("STRATA_USER_NAME", "").lower()) if w}
if USR_NAMES:
    CLASS_MAP["USR"] = CLASS_MAP["USR"] | USR_NAMES


# The first real SUBCLASSES and CUTTERS, and what proved them: preferences are personal taste
# whatever their scope (USR/PREF), decision records are a genre one verb decides (DEC), and the
# fiction canon carries per-setting cutters exactly like LCC's instance tier - FIC/KREVV is the
# Krevven shelf, FIC/VOLDR the Voldranium shelf, and M3's cartridge banks (MEM/CRG.3.431) will
# use the same mechanism, one shelf per session bank.
FIC_CUTTERS = {"krevven": "KREVV", "krevvens": "KREVV", "voldranium": "VOLDR", "thule": "VOLDR",
               "custodian": "VOLDR", "archive": "VOLDR", "opuvvi": "OPUVV", "opuvvian": "OPUVV",
               "gerudo": "OPUVV", "expanse": "OPUVV", "wendfallen": "WENDF",
               "thyravelle": "THYRA", "codex": "THYRA"}
PREF_V = ("prefers", "preference", "preferences", "wants", "likes", "loves", "hates")


def fact_class(fact: str, scope: str) -> str:
    """The shelf mark for a stored fact (PHASE 4.5). Order is deliberate:
    1. USR/PREF - "user" plus a preference verb: taste is personal by nature, first subclass.
    2. DEC      - one decision verb decides the GENRE (unlike USR, where a stray word is noise,
                  "decided" IS the record) - but only when nothing else outscores it.
    3. argmax with a 2-token margin over the runner-up, or the fact stays WILD: it rides every
       unfiltered request exactly as before, and joins no filtered one. Wrong shelves hide
       facts; wild shelves only wait.
    4. A confident USR score still shelves personal; everything else stays wild.
    Subclasses beyond these (MEM/CRG, STR/VER) arrive with the embedder's centroids (Phase 4)
    or a human hand - not with more keywords."""
    toks = set(_norm(fact).split())
    scores = {cls: len(toks & set(words)) for cls, words in CLASS_MAP.items()}
    if "user" in toks and any(v in toks for v in PREF_V):
        return "USR/PREF"
    if scores["DEC"] >= 1 and scores["DEC"] >= max(scores.values()):
        return "DEC"
    ranked = sorted(scores.values(), reverse=True)
    if ranked[0] >= 2 and ranked[0] - ranked[1] >= 2:
        top = max(scores, key=scores.get)
        if top == "FIC":
            cut = next((FIC_CUTTERS[t] for t in toks if t in FIC_CUTTERS), None)
            return f"FIC/{cut}" if cut else "FIC"
        return top
    # NO keywords, no shelf - even for user scope. "The user's dev server was rebooted" is about
    # infrastructure, not the person; wild (unclassified) rides every UNFILTERED request, which is
    # the safe home for an uncertain fact. The USR lane fills on personal SIGNAL (>= 2 tokens),
    # not on scope and not on one stray word like "language" in a technical sentence.
    return "USR" if scores["USR"] >= 2 else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--limit", type=int, default=0, help="max turns this run (0 = all pending)")
    ap.add_argument("--passes", type=int, default=3, help="cold passes per candidate (>= 3 protocol)")
    ap.add_argument("--max-facts", type=int, default=2, help="candidate facts extracted per turn")
    ap.add_argument("--sweep", action="store_true", help="idle-gated batches (hot-loop shape)")
    ap.add_argument("--interval", type=int, default=60, help="--sweep: seconds between idle checks")
    ap.add_argument("--idle-window", type=int, default=240,
                    help="--sweep: seconds the engine must go with NO request at all (server's "
                         "activity.last_request_at) before a batch may start - sustained quiet, "
                         "not quiet-at-the-instant (2026-10-16: the instant version fired into "
                         "every conversational gap and cost the real turns their KV reuse)")
    ap.add_argument("--batches", type=int, default=0, help="--sweep: stop after N batches (0 = forever)")
    ap.add_argument("--extract-only", action="store_true", help="smoke the extractor, skip cold-score")
    ap.add_argument("--no-date", action="store_true",
                    help="the CONTROL arm: extract with the pre-grounding prompt, byte-identical to the "
                         "one every fact now in the store was written with. Dated is the default; this "
                         "flag is what keeps the comparison a single-variable test. Why the date "
                         "matters: temporal reasoning is 0.465, the worst type on the board, and the "
                         "diagnostic names multi-event ORDERING as the mechanism - while a rendered "
                         "memory line carries no date at all, so the fact has to name its own day.")
    ap.add_argument("--no-store", action="store_true", help="score but do not remember/mark")
    ap.add_argument("--dry-run", action="store_true", help="render + count, no engine asks")
    ap.add_argument("--tag", default=datetime.date.today().isoformat())
    a = ap.parse_args()
    a.tag = re.sub(r"[^A-Za-z0-9_-]", "_", a.tag)           # tag lands in filenames (c2 rule)
    if a.passes < 3:
        raise SystemExit("--passes >= 3 is mandatory protocol (V3/C-series: >= 3 passes)")
    url = a.url.rstrip("/")
    state = HERE / "state"
    state.mkdir(exist_ok=True)
    js = (state / f"curate_{a.tag}.jsonl").open("a")

    def rec(obj):
        obj = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "tag": a.tag, **obj}
        js.write(scrub(json.dumps(obj, ensure_ascii=False)) + "\n")   # scrub at the choke
        js.flush()
        print(scrub(json.dumps({k: v for k, v in obj.items() if k not in ("ts", "tag")},
                               ensure_ascii=False))[:220], flush=True)  # point: no field

    st, health = http(url + "/health", timeout=15)
    if st != 200:
        raise SystemExit(f"/health -> {st}: no run")
    model = health.get("model") or "strata"
    st, resp = http(url + "/memory")
    if st != 200:
        raise SystemExit("GET /memory failed - is memory on?")
    stats = resp.get("stats") or {}                        # GET /memory = {"stats": {...}, "memories": [...]}
    db_path = stats.get("db")
    if not db_path:
        raise SystemExit("stats.db missing - cannot mark turns processed")
    print(f"health ok model={model} tag={a.tag} turns_pending={stats.get('turns_pending')} "
          f"memories={stats.get('memories')} channel=off/temp0.8 muted passes={a.passes}")

    tok, tpl = load_tokenizer(), load_template()
    from serve import frontend as F                    # the assert's GROUND TRUTH literal set
    CTRL_LITERALS.extend(sorted({*F.LITERAL_TAGS, *tok.control_tokens}, key=lambda x: -len(x)))

    def ask(text, maxtok, want_geom=None, kind=""):
        """one muted generation; returns (answer, ptok, geom_ok) - transport failure -> (None,..)."""
        body = {"model": model, "messages": [{"role": "user", "content": text}],
                "temperature": 0.8, "reasoning_effort": "off", "max_tokens": maxtok,
                "stream": False}
        st, r = http(url + "/v1/chat/completions", body, headers={"X-Strata-Memory": "off"})
        if st != 200:
            return None, None, False
        ans = (((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        ptok = (r.get("usage") or {}).get("prompt_tokens")
        geom = None if want_geom is None else (ptok == want_geom)
        return ans, ptok, geom

    # ---- extraction ----------------------------------------------------------------------
    processed_ids: list[int] = []
    handled: set[int] = set()      # ids processed THIS run: turns are only marked processed at
    candidates: list[dict] = []    # the END, so op=turns keeps returning them mid-run - without
    seen: set[str] = set()         # this filter the fetch loop re-extracts the same 50 forever
    drained = fails = 0
    batch = 0
    batch_start = time.time()     # sibling protection for supersede: a correction's target must
    while True:                   # predate this batch - candidates from one sweep must not eat
                                  # each other's fresh rows (the 2026-10-12 turn-565-ate-564 bug)
        # idle gate (hot-loop shape): never extract into the middle of someone's request - and
        # "idle" means SUSTAINED quiet, not quiet-at-the-instant. Measured the failure of the
        # instant check the day it shipped its successor (2026-10-16): between interactive turns
        # the engine reads idle for 10-60 s at a time, the sweep fired into every gap, and each
        # small ask shredded the conversation checkpoint - the real turns came back REUSED 0
        # and re-read 246k tokens every time. activity.last_request_at is the server's own
        # record of the last byte it served; requiring it to age --idle-window seconds lets a
        # human work uninterrupted and drains the journal when the machine is truly left.
        # (And the keys this gate first read - top-level "busy"/"queued" - do not exist in
        # /v1/status at all, so the gate never actually blocked: activity.in_flight does.)
        if a.sweep:
            st, svc = http(url + "/v1/status", timeout=15)
            act = svc.get("activity") or {}
            busy = ((act.get("in_flight") or 0) > 0 or bool(svc.get("busy"))
                    or (svc.get("queued") or 0) > 0)
            quiet = time.time() - (act.get("last_request_at") or 0)
            if busy or quiet < a.idle_window:
                print(f"engine busy or only {quiet:.0f}s quiet (wants {a.idle_window}) "
                      f"- sweep waits", flush=True)
                time.sleep(a.interval)
                continue
        st, r = http(url + "/memory", {"op": "turns", "limit": 1000}, timeout=60)
        # limit 1000 = the endpoint's cap (memory.py:368): marking lands only at run end, so a
        # 50-fetch would make batch 2 re-see batch 1's still-unmarked turns and the handled-
        # filter would end the run at the first window (drain409d stopped at exactly 50 this
        # way). One big fetch drains the backlog in one pass; >1000 pending spills to the
        # NEXT run (the handled-filter ends this one), which is what --sweep exists for.
        turns = (r.get("turns") or []) if st == 200 else []
        if a.limit:
            turns = turns[:max(0, a.limit - drained)]
        turns = [t for t in turns if t["id"] not in handled]   # the termination fix
        if not turns:
            break
        for t in turns:
            try:                                    # a poison turn must not kill the whole drain
                prompt = extract_prompt(t, a.max_facts, dated=not a.no_date)
                ids, _ = live_render(tok, tpl, [{"role": "user", "content": prompt}], "off")
            except Exception as e:                  # render assert on unexpected content: record,
                rec({"kind": "extract", "turn_id": t["id"], "render_error":              # noqa: BLE001
                     f"{type(e).__name__}: {e}"[:120]})
                handled.add(t["id"])                # skip ONCE this run (the drain409c lesson:
                print(f"turn {t['id']}: RENDER SKIP (left pending)", flush=True)
                continue                            # never-marked + never-handled = 306K spin
            if a.dry_run:
                print(f"turn {t['id']}: extract ask renders to {len(ids)} tok")
                drained += 1
                continue
            ans, ptok, _ = ask(prompt, 256, kind="extract")
            if ans is None:
                fails += 1
                handled.add(t["id"])                # skip once, same spin lesson
                print(f"turn {t['id']}: extract TRANSPORT FAIL (left pending)", flush=True)
                continue
            n = 0
            new_facts: list[str] = []
            for ln in ans.splitlines():
                m = re.match(r"\s*FACT:\s*(.+?)\s*\|\|\s*PROBE:\s*(.+?)\s*\|\|\s*GOLD:\s*"
                             r"(.+?)\s*\|\|\s*SCOPE:\s*(user|project)\s*$", ln, re.I)
                if not m:
                    continue
                fact, probe, gold, scope = (scrub(g).strip() for g in m.groups())
                key = norm(fact)
                if not fact or not probe or not gold or key in seen:
                    continue
                if norm(gold) in IDK:
                    # a candidate whose GOLD is "idk" is not a fact: the extractor proposed a
                    # question its own answer admits it cannot answer. And gate1.hit()'s idk
                    # guard (spikes/window/gate1_driver.py) returns False BEFORE gold matching,
                    # so an idk-gold could never score a hit - it would silently misclassify.
                    continue
                seen.add(key)
                n += 1
                new_facts.append(fact[:90])                # NOT candidates[-n:]: with n=0 that
                candidates.append({"turn_id": t["id"], "turn_scope": t["scope"], "fact": fact,
                                   "probe": probe, "gold": gold,
                                   "turn_probe": _probe_shape(t["user"], t["assistant"]),
                                   "scope": "user" if scope.lower() == "user" else t["scope"],
                                   "extract_ptok": ptok})  # slice returns the WHOLE list (-0 == 0)
            processed_ids.append(t["id"])
            handled.add(t["id"])
            drained += 1
            rec({"kind": "extract", "turn_id": t["id"], "cands": n, "ptok": ptok,
                 "facts": new_facts})
        batch += 1
        if a.dry_run or (a.batches and batch >= a.batches) or (a.limit and drained >= a.limit):
            break
    if a.dry_run:
        print(f"dry run: {drained} extract asks queued ({len(candidates)} candidates shown "
              f"are placeholders - no engine touched)")
        return 0
    print(f"extracted: {drained} turns drained, {len(candidates)} unique candidates, "
          f"{fails} transport fails", flush=True)
    if a.extract_only:
        for c in candidates:
            print(f"  [{c['turn_id']}] {c['fact'][:100]}")
        return 0

    # ---- cold score ----------------------------------------------------------------------
    stored = {"FILL": 0, "STABILIZE": 0, "CORRECT": 0, "DONT-STORE": 0}
    unscored = 0
    review: list[str] = []
    for c in candidates:
        text = f"{COLD_PREAMBLE}\n{c['probe']}"
        ids, _ = live_render(tok, tpl, [{"role": "user", "content": text}], "off")
        want = len(ids)
        cold = []
        for p in range(a.passes):
            ans, ptok, geom = ask(text, 24, want_geom=want, kind="cold")
            if ans is None:
                rec({"kind": "cold", "turn_id": c["turn_id"], "pass": p + 1, "transport": "fail"})
                continue
            if not geom:                                   # per-ask VOID (V3 precedent): the
                rec({"kind": "cold", "turn_id": c["turn_id"], "pass": p + 1, "void": True,
                     "ptok": ptok, "expect": want})        # candidate's band needs >= 3 alive
                continue
            g = bool(hit(ans, c["gold"]))
            cold.append({"gold_hit": g, "idk": idk_p(ans), "answer": scrub(ans)[:90],
                         "ptok": ptok})
        if len(cold) < a.passes:
            unscored += 1
            cls, mixed, full = "UNSCORED", False, None
        else:
            cls, mixed = classify(cold)
            full = cold
        c["cls"], c["mixed"], c["cold"] = cls, mixed, full
        if cls == "CORRECT" and c.get("turn_probe"):
            # trust-axis policy (measured 2026-10-12): probe-shaped traffic - quiz question in,
            # <=12-word answer out - RESTATES the gold sheet, it does not correct the world. Its
            # CORRECT verdict demotes to FILL: stored, shelved, journaled, but it never gets a
            # supersede target - that is how turn 565 retired turn 564's 40-second-old row. The
            # venue's original call survives in the review line and the probe_demote record.
            rec({"kind": "probe_demote", "turn_id": c["turn_id"], "fact": c["fact"][:80]})
            cls = "FILL"
            c["demoted"] = "probe"
        if cls not in ("UNSCORED", "DONT-STORE") and not a.no_store:
            # §11: DONT-STORE is "log noise, not memory" - the review list carries it, the
            # store does not. (drain409e audit found 5 DONT-STOREs stored by the pre-fix
            # code; they were forgotten by hand the same night, source-tag verified.)
            fields = {"text": c["fact"], "scope": c["scope"],
                      "source": f"curation-{datetime.date.today().isoformat()}",
                      "class": fact_class(c["fact"], c["scope"]),   # PHASE 4.5: shelf at store time
                      "provenance": {"turn_id": c["turn_id"], "probe": c["probe"], "gold": c["gold"],
                                     "cls": cls, "mixed": mixed, "passes": cold, "model": model}}
            body = {"op": "remember", **fields}
            if cls == "CORRECT":
                # job 7 routing: a correction SUPERSEDES the fact it contradicts (found by content
                # address) instead of parking a 121st near-dupe beside it in recall
                old = supersede_id(url, c["fact"], c["scope"], rec, min_created=batch_start)
                if old:
                    body = {"op": "correct", "id": old, **fields}
                    c["supersedes"] = old
            st, r = http(url + "/memory", body, timeout=60)
            if st == 400 and body["op"] == "correct":         # server predates op=correct: plain store
                st, r = http(url + "/memory", {"op": "remember", **fields}, timeout=60)
                c["supersedes"] = "deferred:server-predates-correct"
            c["mem_id"] = (r or {}).get("id")
            if st != 200:
                rec({"kind": "remember", "turn_id": c["turn_id"], "http": st,
                     "err": str(r)[:120]})
        stored[cls] = stored.get(cls, 0) + 1
        rec({"kind": "score", "turn_id": c["turn_id"], "cls": cls, "mixed": mixed,
             "fact": c["fact"][:90], "gold": c["gold"][:40], "supersedes": c.get("supersedes"),
             "hits": [x["gold_hit"] for x in cold]})
        review.append(f"[{cls}{'/mixed' if mixed else ''}] (turn {c['turn_id']}) {c['fact']}\n"
                      f"    probe: {c['probe']}  gold: {c['gold']}  "
                      f"cold: {' | '.join(x['answer'][:40] for x in cold)}")
    if processed_ids and not a.no_store:                   # the store's own hygiene flag
        from serve.memory import attach
        store_dir = stats.get("dir") or (os.path.dirname(db_path) if db_path else "")
        if not store_dir:
            raise SystemExit("stats.dir missing - cannot mark turns processed")
        # ADR-002: the PG twin of the old sqlite3.connect - and the ATTACH law (measured, R0b)
        # is what makes this safe mid-server-run: the sweep never stops a live cluster.
        with attach(store_dir, autocommit=True) as con:
            with con.cursor() as cur:
                cur.executemany("update turns set processed=1 where id = %s",
                                [(int(i),) for i in processed_ids])

    # ---- verdict block (the part that is safe to commit: counts, no personal text) --------
    pend_after = (http(url + "/memory", timeout=15)[1].get("stats") or {}).get("turns_pending")
    lines = [f"curate verdict tag={a.tag} channel=off/temp0.8 muted passes={a.passes} "
             f"stored={'off' if a.no_store else 'on'}",
             f"  drained={drained} extract_fails={fails} candidates={len(candidates)} "
             + " ".join(f"{k}={v}" for k, v in stored.items()) + f" UNSCORED={unscored}",
             f"  marked_processed={len(processed_ids) if not a.no_store else 0} "
             f"db_pending_after={pend_after}"]
    txt = "\n".join(lines)
    print("\n" + scrub(txt))
    (state / f"curate_{a.tag}.review.txt").write_text(scrub("\n".join(review) + "\n\n" + txt + "\n"),
                                                      encoding="utf-8")
    js.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
