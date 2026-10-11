#!/usr/bin/env python3
"""spikes/window/gate1_driver.py - Gate-1 text-memory run: the plumbing half.

Measurement design (agreed 2026-10-11): ALL question-asking happens as USER PASTES into the
Strata webapp chat (same channel as the cold baseline from today - apples-to-apples). This
script never asks the model a measurement question. It does:
  teach      - POST /memory {"op":"remember"} for every CSV row (scope user, source gate1),
               records returned ids to state/gate1_ids.json for clean forget-after
  verify     - GET /memory {"op":"search"} per question: teaches must be findable in the
               store before the user pastes a single question (store-level sanity, no chat)
  telemetry  - GET /memory stats + /metrics prefix-cache counters to state/telemetry_TAG.json,
               run before/after each user paste block (brackets engine state; informational -
               the pre-registered pass conditions are marker/recall correctness, not counters)
  score      - parse the user's pasted answers file ("N. answer" or bare lines in order),
               strict word-boundary scoring vs the CSV golds (K-stripped FAA variants,
               st/period normalization, ordinal day dates), compare against the COLD verdict
               in eval_candidates.tsv (same question text) -> state/gate1_score.json:
               recall rate, confabulation rate, per-harness and per-kind splits, miss list
  smoke      - end-to-end drill on a live server with 3 invented facts (teach -> verify ->
               one real chat completion WITH memory injected -> score it -> FORGET all 3).
               Against production :8004 the smoke facts live and die in minutes.

Modes that touch chat (smoke) hit /v1/chat/completions directly - fresh conversation,
reasoning off, temp 0, exactly like probe_driver.py's chat().
"""
import argparse, csv, json, re, sys, time, urllib.request as U, urllib.parse as UP
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"


def http_json(url, payload=None, method=None, timeout=300):
    data = json.dumps(payload).encode() if payload is not None else None
    req = U.Request(url.rstrip("/") + "/memory", data=data,
                    method=method or ("POST" if data else "GET"))
    with U.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def chat(url, messages, timeout=600):
    body = {"messages": messages, "temperature": 0, "reasoning_effort": "off",
            "max_tokens": 200}
    req = U.Request(url.rstrip("/") + "/v1/chat/completions",
                    data=json.dumps(body).encode(), method="POST",
                    headers={"Content-Type": "application/json"})
    t0 = time.time()
    with U.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read())
    return j["choices"][0]["message"]["content"], time.time() - t0, j.get("usage", {})


def rows():
    with open(HERE / "eval_questions.csv") as f:
        lines = [l for l in f if not l.startswith("#")]
    rd = csv.reader(lines)
    hdr = next(rd)
    return [dict(zip(hdr, r)) for r in rd if r]


# ---------- scoring core (strict word-boundary, same family as probe_driver.hit_strict) ----
def norm(x):
    s = x.lower().replace(".", " ").replace(",", " ")
    s = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^st ", "saint ", s)
    s = re.sub(r"^mt ", "mount ", s)
    return s


def variants(tok):
    """accept-list for one expected token: word-boundary phrase + known spelling variants."""
    t = tok.strip()
    out = [t]
    if re.fullmatch(r"K?[A-Z0-9]{3}", t):                     # FAA identifiers
        out += ["K" + t] if not t.startswith("K") else [t[1:]]
    m = re.fullmatch(r"(January|February|M|March|April|May|June|July|August|September|October|November|December) (\d{1,2}) (\d{4})", t)
    if m:                                                      # date rows: day-first too
        out.append(f"{m.group(2)} {m.group(1)} {m.group(3)}")
    return out


def hit(answer, expected_field):
    a = norm(answer)
    if a in ("idk", "i don't know", "i do not know", "unknown", "don't know", "no answer", ""):
        return False
    for tok in expected_field.split(","):
        ok = False
        for v in variants(tok):
            pat = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in norm(v).split()) + r"(?!\w)"
            if re.search(pat, a):
                ok = True
                break
        if not ok:
            return False                                       # every listed token must appear
    return True


# ---------- teach ----------
def teach(url):
    STATE.mkdir(exist_ok=True)
    ids = []
    for i, r in enumerate(rows(), 1):
        fact = f"{r['question']} Answer: {r['expected_answer_contains']}."
        resp = http_json(url, {"op": "remember", "text": fact, "scope": r["scope"],
                               "source": "gate1-final-2026-10-11"})
        ids.append({"n": i, "id": resp.get("id"), "question": r["question"]})
        if resp.get("id") is None:
            print(f"WARN row {i}: no id in response {str(resp)[:120]}")
    (STATE / "gate1_ids.json").write_text(json.dumps(ids, indent=1))
    st = http_json(url)
    print(f"taught {len(ids)} facts; store stats: {json.dumps(st.get('stats', st))[:200]}")
    print("ids ->", STATE / "gate1_ids.json", "(keep for clean forget after the run)")


def verify(url):
    bad = []
    for i, r in enumerate(rows(), 1):
        key = norm(r["question"]).split()[-3]                  # distinctive word of the question
        resp = http_json(url.rstrip("/") + f"/memory?q={UP.quote(key)}")
        hits = [h.get("text", "") for h in resp.get("hits", [])]
        if not any(norm(r["expected_answer_contains"]) in norm(h) for h in hits):
            bad.append(i)
    print(f"verify: {len(rows()) - len(bad)}/{len(rows())} searchable; unsane rows: {bad or 'none'}")


# ---------- telemetry ----------
def telemetry(url, tag):
    STATE.mkdir(exist_ok=True)
    stats = http_json(url)
    try:
        req = U.Request(url.rstrip("/") + "/metrics")
        with U.urlopen(req, timeout=20) as r:
            mtxt = r.read().decode()
        counters = {}
        for line in mtxt.splitlines():
            m = re.match(r"([a-z_:]*prefix_cache[a-z_]*[a-z_:]*)\s+(\d+)", line.strip())
            if m:
                counters[m.group(1)] = int(m.group(2))
    except Exception as e:
        counters = {"error": repr(e)[:120]}
    out = {"tag": tag, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "memory": stats.get("stats", stats), "prefix_counters": counters}
    p = STATE / f"telemetry_{tag}.json"
    p.write_text(json.dumps(out, indent=1))
    print(f"telemetry[{tag}]: {json.dumps(out)[:260]}")


# ---------- score ----------
def cold_map():
    m = {}
    with open(HERE / "eval_candidates.tsv") as f:
        next(f)
        for line in f:
            p = line.rstrip("\n").split("\t")
            m[p[3]] = {"verdict": p[6], "kind": p[2], "gold": p[4]}
    return m


def parse_answers(path):
    """accepts 'N. answer' / 'N) answer' / 'N=answer' numbered lines, OR bare lines in order."""
    out, auto = {}, 0
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\s*[.)\-=:\]]\s*(.+)$", line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
        else:
            auto += 1
            out.setdefault(auto, line)
    return out


def score(path):
    ans = parse_answers(path)
    cold, R = cold_map(), rows()
    per = []
    for i, r in enumerate(R, 1):
        a = ans.get(i)
        c = cold.get(r["question"], {})
        hot = hit(a, r["expected_answer_contains"]) if a else False
        was_known = c.get("verdict") == "known"
        per.append({"n": i, "kind": c.get("kind", "?"), "harness": r["asked_cold_harness"],
                    "answer": a, "hot_correct": hot, "cold_verdict": c.get("verdict", "?"),
                    "memory_effect": "P" if (hot and not was_known) else
                                     ("-" if hot else ("COLD-ONLY" if not a else "MISS"))})
    got = [p for p in per if p["answer"] is not None]
    targets = [p for p in got if p["cold_verdict"] != "known"]
    rec = sum(1 for p in targets if p["hot_correct"])
    conf = sum(1 for p in got if p["cold_verdict"] == "known" and p["hot_correct"])
    def split(key):
        o = {}
        for p in targets:
            b = o.setdefault(p[key], [0, 0]); b[1] += 1; b[0] += 1 if p["hot_correct"] else 0
        return {k: f"{v[0]}/{v[1]}" for k, v in sorted(o.items())}
    summ = {"parsed": len(got), "of_rows": len(R), "unknown_targets": len(targets),
            "recall_rate": f"{rec}/{len(targets)}" if targets else "n/a",
            "recall_by_kind": split("kind"), "recall_by_harness": split("harness"),
            "confab_correct_of_known": f"{conf}/{len(got) - len(targets)}" if len(got) != len(targets) else "n/a",
            "misses": [p["n"] for p in targets if not p["hot_correct"]]}
    STATE.mkdir(exist_ok=True)
    (STATE / "gate1_score.json").write_text(json.dumps({"summary": summ, "rows": per}, indent=1))
    print(json.dumps(summ, indent=1))
    print("full detail ->", STATE / "gate1_score.json")


# ---------- smoke ----------
def smoke(url):
    STATE.mkdir(exist_ok=True)
    nonce = format(int(time.time()) % 100000, "05d")
    facts = [f"The {x[0]} of Smokeport is Zivvleax{nonce}-{k}. Question: what is the {x[0]} of Smokeport? Answer: Zivvleax{nonce}-{k}."
             for k, x in enumerate([("mayor",), ("bridge",), ("brew",)])]
    ids = [http_json(url, {"op": "remember", "text": t, "scope": "user", "source": "smoke"})["id"]
           for t in facts]
    print("smoke taught ids:", ids)
    v = http_json(url.rstrip("/") + f"/memory?q=Zivvleax{nonce}&limit=9")
    print("smoke search hits:", len(v.get("hits", [])), "(expect 3)")
    q = f"What is the bridge of Smokeport? Answer from the information you were given."
    ans, secs, usage = chat(url, [{"role": "user", "content": q}])
    ok = f"Zivvleax{nonce}-1" in ans
    print(f"smoke chat: '{ans[:120]}' ({secs:.1f}s, ptok={usage.get('prompt_tokens')}) "
          f"-> {'PASS (recall reached the answer)' if ok else 'FAIL'}")
    for i in ids:
        http_json(url, {"op": "forget", "id": i})
    left = http_json(url.rstrip("/") + f"/memory?q=Zivvleax{nonce}")
    print(f"smoke forgotten; search hits now: {len(left.get('hits', []))} (expect 0)")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["teach", "verify", "telemetry", "score", "smoke"])
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--answers", default=str(STATE / "gate1_answers.txt"))
    ap.add_argument("--tag", default="x")
    a = ap.parse_args()
    {"teach": lambda: teach(a.url), "verify": lambda: verify(a.url),
     "telemetry": lambda: telemetry(a.url, a.tag), "score": lambda: score(a.answers),
     "smoke": lambda: smoke(a.url)}[a.mode]()
