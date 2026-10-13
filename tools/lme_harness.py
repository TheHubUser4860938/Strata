#!/usr/bin/env python3
"""LongMemEval-V1 venue harness for Strata's built-in memory (beta item F, 2026-10-13).

The claim F must measure before beta says "yes": the server's own recall answers real
long-term-memory questions from a history it ingested. The benchmark (LongMemEval, ICLR
2025, MIT; data at huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) fits the
machinery one-to-one: 500 questions, each with a timestamped chat history, a gold answer,
turn-level `has_answer` gold labels, and 30 abstention ids ending in `_abs`.

Design decisions, recorded where they bite:

- PER-QUESTION SCOPE (`lme_<question_id>`). Personas differ per question and the USR lane
  is global; pinning each question's facts to its own project scope is the contamination
  guard the plan pre-registered. The server recalls with the same header any client uses
  (X-Strata-Memory-Scope), so this measures the product path, not a harness-side RAG.
- INGEST = `op remember` per turn, date-prefixed (temporal reasoning needs dates and the
  store's created_at would say "today"). Both roles: `single-session-assistant` questions
  hide their evidence in assistant turns. Pure SQL - the engine never runs for ingest,
  and the returned row id maps every recall hit back to its turn and session, which is
  how top-1/top-3 (the Primnox metric) is scored against `has_answer` and
  `answer_session_ids`.
- ANSWERS ride the live engine: fresh conversation per ask, temp 0.8 (never 0), >=3
  repeats per question with a majority verdict, probe max_tokens 500. Muted vs unmuted is
  the A/B: --muted-control runs the same question with memory off for the cold baseline.
- JUDGE: the OFFICIAL checker prompt (type-specific templates, verbatim from the repo's
  evaluate_qa.py) with the official decoding (temp 0, 10 tokens - the no-zero discipline
  governs the answer under measurement, not the scorer), but the judge MODEL is this server
  with memory MUTED, since the box has no OpenAI key. The metrics line says exactly that:
  paper-comparable prompt and params, non-paper model (disclosure is part of the claim).
- Scrub at the choke: every string written out passes the pipe-splitter (the serving
  template's delimiter must never re-enter any file a later prompt reads back).

Venue-day protocol: dedicated store (memory.dir to a venue dir, max_memories 0 - the
oracle set alone is ~6k rows), nothing else on the port, pilot --limit 5 first, then the
full run backgrounded (resumable: completed questions are skipped from --out).

Usage:
  python3 tools/lme_harness.py --data longmemeval_oracle.json --out lme_oracle.jsonl \
      [--url http://127.0.0.1:8005] [--limit N] [--repeats 3] [--recall-k 10] \
      [--types single-session-user,...] [--skip-ingest] [--no-judge] [--muted-control]
"""
from __future__ import annotations

import argparse, json, re, sys, time, urllib.error, urllib.parse, urllib.request as U

DATE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})")
ANSWER_RULES = {
    "direct": ("Answer in a few words from the memory you carry. If it does not hold what "
               "is needed, reply exactly: idk"),
    # The benchmark authors' own recommendation ("con": extract useful information first, then
    # reason over it) - the oracle pilot (2026-013-13) showed the failure it targets: top-1
    # recall was 3/3 while one answer still picked the wrong fact out of the block.
    "con": ("First quote the memory lines that bear on the question, then give the final answer "
            "in a few words on its own last line. If the memory does not hold what is needed, "
            "reply exactly idk on the last line."),
}


def scrub(s: str) -> str:
    """The standing rule for anything that leaves into a file a prompt may read back: the
    chat template's delimiter can never travel intact through venue output."""
    return (s or "").replace("<|", "< |").replace("|>", "| >")


def post(url: str, path: str, body: dict, headers: dict | None = None, timeout: float = 240.0):
    req = U.Request(url + path, data=json.dumps(body).encode(), method="POST",
                    headers={"Content-Type": "application/json", **(headers or {})})
    with U.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get(url: str, path: str, params: dict, headers: dict | None = None, timeout: float = 60.0):
    q = urllib.parse.urlencode(params)
    req = U.Request(f"{url}{path}?{q}", headers=headers or {})
    with U.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def day_of(date_str: str) -> str:
    m = DATE_RE.match(date_str or "")
    return m.group(1) if m else ""


def ingest(url: str, q: dict, scope: str) -> dict[int, tuple[bool, str]]:
    """Remember every turn of every haystack session, date-prefixed, into the question's own
    scope. Returns row-id -> (has_answer, session_id) so recall hits score turn- AND
    session-level."""
    where: dict[int, tuple[bool, str]] = {}
    for si, session in enumerate(q["haystack_sessions"]):
        sid = q["haystack_session_ids"][si]
        day = day_of(q["haystack_dates"][si])
        for turn in session:
            text = turn.get("content") or ""
            if not text.strip():
                continue
            lead = f"[{day}] " if day else ""
            if turn.get("role") != "user":
                lead += "The assistant told the user: "
            # the store takes one fact up to 4,000 chars; LongMemEval haystack turns - mostly
            # assistant answers - run longer than that, so a long turn is chunked at word
            # boundaries and EVERY chunk carries the turn's gold: an answer that lands in chunk
            # 3 of a turn must still count as a hit, and the session-level score is unchanged
            full = lead + text
            pieces, start = [], 0
            while start < len(full):
                cut = min(len(full), start + 3900)
                if cut < len(full):
                    cut = max(full.rfind(" ", start, cut), start + 1)   # never mid-word
                    pieces.append(full[start:cut])
                    start = cut + 1        # the boundary space is consumed: the store strips
                                           # leading whitespace, so it must not ride the next piece
                else:
                    pieces.append(full[start:cut])
                    start = cut
            for piece in pieces:
                r = post(url, "/memory", {"op": "remember", "text": piece, "scope": scope})
                rid = r.get("id")
                if rid is not None:
                    where[rid] = (bool(turn.get("has_answer")), sid)
    return where


def recall(url: str, q: dict, scope: str, where: dict, k: int) -> dict:
    """The Primnox metric on the server's own search: top-1/top-3 at turn level (the first
    / first-three hits carrying a has_answer turn) and at session level (hits from an
    answer_session). Abstention questions have no gold location and report null, as the
    official retrieval eval skips them."""
    # GET /memory?q= answers under "hits" (the list endpoint uses "memories") - reading the
    # wrong key would score a quiet zero instead of an honest miss.
    hits = get(url, "/memory", {"q": q["question"], "scope": scope, "limit": k},
               headers={"Accept": "application/json"}).get("hits", [])
    ids = [h["id"] for h in hits]
    abstain = q["question_id"].endswith("_abs")
    gold_sids = set(q.get("answer_session_ids") or [])

    def flag(fn):
        if abstain or not ids:
            return None
        vals = [where.get(i, (False, "")) for i in ids[:k]]
        return fn(vals)

    return {"ids": ids,
            "turn_top1": flag(lambda v: v and v[0][0]),
            "turn_top3": flag(lambda v: any(x[0] for x in v[:3])),
            "session_top1": flag(lambda v: v and v[0][1] in gold_sids),
            "session_top3": flag(lambda v: any(x[1] in gold_sids for x in v[:3]))}


def ask(url: str, q: dict, scope: str, muted: bool, style: str = "direct") -> str:
    """One fresh conversation, one answer. The server injects from the question's own scope
    (that IS the product path); a muted control request carries the header that turns
    memory off, and the cold baseline gets no scope at all."""
    hdrs = ({"X-Strata-Memory": "off"} if muted
            else {"X-Strata-Memory-Scope": scope})
    body = {"model": "", "messages": [{"role": "user",
                                       "content": f"{q['question']}\n\n{ANSWER_RULES[style]}"}],
            "max_tokens": 500, "temperature": 0.8, "reasoning_effort": "off"}
    r = post(url, "/v1/chat/completions", body, headers=hdrs)
    return (r["choices"][0]["message"].get("content") or "").strip()


def anscheck_prompt(qtype: str, question: str, answer: str, response: str, abstain: bool) -> str:
    """The benchmark's own checker prompt, VERBATIM from LongMemEval src/evaluation/
    evaluate_qa.py::get_anscheck_prompt (MIT, xiaowu0162) - type-specific templates: temporal
    questions tolerate off-by-one day counts, knowledge-update accepts stale info beside the
    updated answer, preference questions are judged as a rubric, abstention asks only whether
    the model spotted the question as unanswerable. Using theirs verbatim is what lets F's
    accuracy line sit beside the paper's tables; only the judge MODEL is local (disclosed).
    The 32 counting questions in longmemeval_oracle carry answer as an int - the official code
    formats it into an f-string; we concatenate, so coerce at the entry (crash of the second
    full run, 2026-10-13, question ~60: TypeError mid-judge after the answer was already
    generated)."""
    question, answer = str(question), str(answer)
    if abstain:
        return ("I will give you an unanswerable question, an explanation, and a response from a "
                "model. Please answer yes if the model correctly identifies the question as "
                f"unanswerable. The model could say that the information is incomplete, or some "
                f"other information is given but the asked information is not.\n\nQuestion: {question}"
                f"\n\nExplanation: {answer}\n\nModel Response: {response}\n\nDoes the model correctly "
                "identify the question as unanswerable? Answer yes or no only.")
    if qtype in ("single-session-user", "single-session-assistant", "multi-session"):
        core = ("I will give you a question, a correct answer, and a response from a model. Please "
                "answer yes if the response contains the correct answer. Otherwise, answer no. If the "
                "response is equivalent to the correct answer or contains all the intermediate steps "
                "to get the correct answer, you should also answer yes. If the response only contains "
                "a subset of the information required by the answer, answer no.")
    elif qtype == "temporal-reasoning":
        core = ("I will give you a question, a correct answer, and a response from a model. Please "
                "answer yes if the response contains the correct answer. Otherwise, answer no. If the "
                "response is equivalent to the correct answer or contains all the intermediate steps "
                "to get the correct answer, you should also answer yes. If the response only contains "
                "a subset of the information required by the answer, answer no. In addition, do not "
                "penalize off-by-one errors for the number of days. If the question asks for the "
                "number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., "
                "predicting 19 days when the answer is 18), the model's response is still correct.")
    elif qtype == "knowledge-update":
        core = ("I will give you a question, a correct answer, and a response from a model. Please "
                "answer yes if the response contains the correct answer. Otherwise, answer no. If the "
                "response contains some previous information along with an updated answer, the "
                "response should be considered as correct as long as the updated answer is the "
                "required answer.")
    elif qtype == "single-session-preference":
        core = ("I will give you a question, a rubric for desired personalized response, and a "
                "response from a model. Please answer yes if the response satisfies the desired "
                "response. The model does not need to reflect all the points in the rubric. The "
                "response is correct as long as it recalls and utilizes the user's personal "
                "information correctly.")
    else:
        raise ValueError(f"no official checker template for question_type {qtype!r}")
    tail = ("\n\nQuestion: " + question + "\n\nCorrect Answer: " + answer +
            "\n\nModel Response: " + response +
            "\n\nIs the model response correct? Answer yes or no only.")
    return core + tail


def judge(url: str, q: dict, hypothesis: str) -> bool:
    """The official checker, run locally: same prompt and decoding as the paper's gpt-4o judge
    (temp 0, 10 max tokens - the no-zero discipline governs the answer UNDER measurement, not
    the scorer), but the judge model is this server with memory MUTED (it sees only the three
    texts), which the metrics file discards no ambiguity about. A failed call counts incorrect."""
    prompt = anscheck_prompt(q["question_type"], q["question"], q["answer"],
                             hypothesis or "(empty)", q["question_id"].endswith("_abs"))
    try:
        r = post(url, "/v1/chat/completions",
                 {"model": "", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 10, "temperature": 0, "reasoning_effort": "off"},
                 headers={"X-Strata-Memory": "off"})
        out = (r["choices"][0]["message"].get("content") or "").strip().lower()
    except Exception:                                       # noqa: BLE001 - a failed judge says no
        return False
    return "yes" in out                                    # the official eval's own rule


def load_map(map_path: str) -> dict[str, dict[int, tuple[bool, str]]]:
    """Question -> row-id -> (has_answer, session_id), persisted at ingest so a resumed run
    never re-ingests and --skip-ingest never scores against a guessed map (the store does
    not carry the benchmark's gold labels; this sidecar does)."""
    maps: dict[str, dict[int, tuple[bool, str]]] = {}
    try:
        for line in open(map_path):
            o = json.loads(line)
            maps[o["question_id"]] = {int(kk): (v[0], v[1]) for kk, v in o["where"].items()}
    except FileNotFoundError:
        pass
    return maps


def run(url: str, data: list[dict], out_path: str, repeats: int, k: int, types: set,
        skip_ingest: bool, use_judge: bool, muted_control: bool, limit: int,
        style: str = "direct", map_path: str | None = None, log=print) -> dict:
    done, map_path = {}, map_path or out_path + ".map"
    try:
        for line in open(out_path):
            o = json.loads(line)
            done.setdefault(o["question_id"], set()).add(o["repeat"])
    except FileNotFoundError:
        pass
    maps = load_map(map_path)
    stats, started = {"questions": 0, "lines": 0}, time.time()
    with open(out_path, "a") as out, open(map_path, "a") as mapf:
        for q in data:
            if limit and stats["questions"] >= limit:
                break
            qid, qtype = q["question_id"], q["question_type"]
            if types and qtype not in types:
                continue
            if qid.endswith("_abs"):
                qtype = "abstention"
            scope = f"lme_{qid}"
            if len(done.get(qid, ())) >= repeats:
                continue
            if qid in maps:
                where = maps[qid]
            elif skip_ingest:
                raise SystemExit(f"{qid}: no gold map for {qid} - --skip-ingest needs a map "
                                 f"from an earlier run ({map_path})")
            else:
                where = ingest(url, q, scope)
                maps[qid] = where
                mapf.write(json.dumps({"question_id": qid,
                                       "where": {str(i): list(v) for i, v in where.items()}}) + "\n")
                mapf.flush()
                log(f"{qid} ({qtype}): ingested {len(where)} turns")
            rec = recall(url, q, scope, where, k)
            correct_votes = []
            for rep in range(repeats):
                if rep in done.get(qid, ()):
                    continue
                hyp = scrub(ask(url, q, scope, muted=False, style=style))
                cold = scrub(ask(url, q, scope, muted=True)) if muted_control else None
                ok = judge(url, q, hyp) if use_judge else None
                if ok is not None:
                    correct_votes.append(ok)
                obj = {"question_id": qid, "type": qtype, "repeat": rep,
                       "hypothesis": hyp, "gold": scrub(q["answer"]), "correct": ok,
                       "cold_hypothesis": cold, "judge": None if ok is None else
                       "official LongMemEval checker prompt; local judge (same model, memory "
                       "muted, temp 0, 10 tokens - paper-comparable decoding, non-paper model)",
                       **{kk: rec[kk] for kk in ("turn_top1", "turn_top3", "session_top1",
                                                 "session_top3")}, "recall_ids": rec["ids"]}
                out.write(json.dumps(obj, ensure_ascii=False) + "\n")
                out.flush()
                stats["lines"] += 1
            stats["questions"] += 1
            majority = (sum(correct_votes) * 2 > len(correct_votes)) if len(correct_votes) >= 3 else None
            log(f"{qid} ({qtype}): top1={rec['turn_top1']} top3={rec['turn_top3']} "
                f"{'majority=' + str(majority) if majority is not None else ''}")
    print(f"[lme] {stats['questions']} questions, {stats['lines']} lines, "
          f"{time.time() - started:.0f} s; metrics: {metrics(out_path)}")
    return {**stats, "metrics": metrics(out_path)}


def metrics(out_path: str) -> dict:
    """Aggregate what the run wrote: recall top-1/top-3 (turn and session) and judge accuracy,
    overall and per type. Abstention questions keep recall nulls (skipped in the means, as the
    official retrieval eval does); the judge line says the judge was local."""
    rows = [json.loads(l) for l in open(out_path)]
    out = {"lines": len(rows), "judge": rows[0]["judge"] or "none (--no-judge)"}

    def agg(sub):
        r = {}
        for key in ("turn_top1", "turn_top3", "session_top1", "session_top3"):
            vals = [x[key] for x in sub if x[key] is not None]
            r[key] = round(sum(vals) / len(vals), 3) if vals else None
        cor = [x["correct"] for x in sub if x["correct"] is not None]
        r["accuracy"] = round(sum(cor) / len(cor), 3) if cor else None
        # con-style hypotheses can MENTION idk while answering; only the final line is the answer
        def is_idk(x):
            return "idk" in (x["hypothesis"] or "").strip().lower().split("\n")[-1]
        r["idk_rate"] = round(sum(1 for x in sub if is_idk(x)) / len(sub), 3) if sub else None
        return r

    out["overall"] = agg(rows)
    types = sorted({r["type"] for r in rows})
    out["by_type"] = {t: agg([r for r in rows if r["type"] == t]) for t in types}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LongMemEval-V1 venue harness for Strata memory")
    ap.add_argument("--data", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--out", default="lme_hypotheses.jsonl")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--recall-k", type=int, default=10)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--types", default="", help="comma-separated question_type filter")
    ap.add_argument("--skip-ingest", action="store_true", help="rows already in the store")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--muted-control", action="store_true", help="also ask each question cold")
    ap.add_argument("--style", choices=("direct", "con"), default="direct",
                    help="answer instruction: direct, or the authors' recommended extract-then-answer")
    ap.add_argument("--map", dest="map_path", default=None,
                    help="gold-map sidecar (default: OUT.map) - point at an earlier run's map to --skip-ingest")
    a = ap.parse_args(argv)
    data = json.load(open(a.data))
    st = get(a.url, "/memory", {"limit": 1}).get("stats", {})
    cap = st.get("max_memories") or 0
    if cap:
        print(f"[lme] store cap {cap} (rows {st.get('memories')}): a full run wants max_memories 0 "
              "on a dedicated venue store - the oracle set alone is ~6k rows", file=sys.stderr)
    got = run(a.url, data, a.out, a.repeats, a.recall_k, {t.strip() for t in a.types.split(",")
            if t.strip()}, a.skip_ingest, not a.no_judge, a.muted_control, a.limit, a.style, a.map_path)
    with open(a.out + ".metrics.json", "w") as f:                  # the venue record
        json.dump({"data": a.data, "url": a.url, "repeats": a.repeats, "recall_k": a.recall_k,
                   "muted_control": a.muted_control, "style": a.style, **got}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
