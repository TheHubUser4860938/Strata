#!/usr/bin/env python3
"""spikes/window/probe_driver.py - the coordinated-window probe matrix against :8005, one phase
per invocation so the human restarts can fall BETWEEN phases (the queued-orchestration protocol
this fork already uses). Every answer, every token count, every prefix-cache delta goes to
spikes/window/state/<phase>.jsonl as the experiment record - the scripts never decide pass/fail
silently: `--phase report` reads the JSONLs and prints the verdicts with their pre-registered
conditions, which are recorded here and in markers.json so nobody can move the goalposts later.

Phases (run order across the window):
  d2-baseline    BEFORE the patched copy is booted: marker questions + control markers +
                 positive control (fact shown as text). Expect: baseline marker FAILS.
  b1             session-file round trip: two-turn conversation with a unique fact -> slot save
                 -> strata_session dissect (recorded) -> FRESH conversation asks the question
                 (must FAIL: no memory) -> restore -> ask again (the recall test) with the
                 prefix-cache counter before/after (reuse vs re-prefill is the real question).
  a-prep         save a marker conversation, write state/a_pending.json; the user then restarts
                 the dev engine; the recall-after-restart check runs in...
  a-check        ...this phase, after the restart: restore + answer + verdict (Spike A remainder:
                 cross-restart AND cross-conversation restore from the same file).
  d2-treatment   AFTER booting with --ple-gguf = patched copy: the same marker questions + the
                 never-written control markers. PASS = every marker question hits AND no control
                 hits (positive control must hit too; it re-sends the text so it always should).
  selftest       pure-function checks (scorer, question construction) - no server needed.
  report         summarize every state JSONL and print the pre-registered verdicts.

Controls baked in (from the plan's D3 protocol): every request carries X-Strata-Memory: off
(tier-3 text recall must never fake a reflex), reasoning_effort "off" (thinking mode returns
content=null on this server), temperature 0, fresh conversation per probe (a new first-user
message), and the negative-control markers that were NEVER written to the table.
"""
from __future__ import annotations
import argparse, json, re, sys, time, urllib.request as U
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
BASE = "http://127.0.0.1:8005"


def http(url, payload=None, headers=None, method=None, timeout=300):
    data = json.dumps(payload).encode() if payload is not None else None
    req = U.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with U.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return raw


def metrics(url):
    """prefix-cache hits from /metrics, whatever exact spelling this engine uses (docs say
    strata:prefix_cache_hits_total; capture any line with prefix_cache_hits + a number)."""
    try:
        txt = http(url.rstrip("/") + "/metrics", headers={"Accept": "text/plain"})
        m = re.search(r"[a-z_:]*prefix_cache_hits[a-z_:]*\s+(\d+)", txt)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def chat(url, msgs, extra_headers=None, checkpoint=False):
    # one-shot probe turns ask for strata_checkpoint:false (server.py L1006: their turn is
    # never extended, so no conversation checkpoint). EXCEPTION: any turn whose conversation
    # must survive a /slots save - the engine refuses SAVE with "no complete session to save"
    # (seen live 2026-10-11 11:47Z, b1_cartridge_19246.bin) when the newest conversation is
    # one-shot. Fact turns therefore pass checkpoint=True; question turns stay one-shot.
    body = {"messages": msgs, "temperature": 0.0, "max_tokens": 64, "reasoning_effort": "off"}
    if not checkpoint:
        body["strata_checkpoint"] = False
    hdrs = {"X-Strata-Memory": "off", "Accept": "application/json"}
    hdrs.update(extra_headers or {})
    t0 = time.time()
    raw = http(url.rstrip("/") + "/v1/chat/completions", body, hdrs)
    d = json.loads(raw)
    c = d["choices"][0]["message"]
    ans = (c.get("content") or "").strip()
    return ans, time.time() - t0, d.get("usage", {})


def hit_strict(answer, golds):
    a = " ".join(answer.split()).lower()
    for g in golds:
        if re.search(r"(?<!\w)" + re.escape(" ".join(g.lower().split())) + r"(?!\w)", a):
            return True
    return False


def record(phase, row):
    STATE.mkdir(exist_ok=True)
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **row}
    with (STATE / f"{phase}.jsonl").open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[{phase}] {json.dumps(row, ensure_ascii=False)[:160]}")


def load_markers():
    return json.loads((HERE / "markers.json").read_text())


def slot(url, action, filename=None):
    body = {"filename": filename} if filename else {}
    try:
        return http(f"{url.rstrip('/')}/slots/0?action={action}", body if action else None,
                    method="POST")
    except Exception as e:
        return f"__ERROR__ {repr(e)[:180]}"


def phase_d2(url, phase, treatment):
    for pr in load_markers()["pairs"]:
        # control golds = the SAME expect list: a control question hitting means the knowledge
        # leaked onto a never-written key (or the model guessed the exact names) - both fail.
        for kind, q, golds in (("marker", pr["question"], pr["expect"]),
                               ("control", pr["control_question"], pr["expect"]),
                               ("positive", pr["fact_text"] + " Given only that, answer: " + pr["question"],
                                pr["expect"])):
            # all three kinds run in BOTH d2 phases: the positive control is a capability check
            # every boot should pass, and the control marker must fail on stock AND patched boots
            # (a hit there means guessing - the marker principle).
            ans, secs, usage = chat(url, [{"role": "user", "content": q}])
            ok = hit_strict(ans, golds) if golds else None
            record(phase, {"pair": pr["name"], "kind": kind, "q": q, "answer": ans,
                           "correct": ok, "secs": round(secs, 2), "ptok": usage.get("prompt_tokens")})


def phase_b1(url):
    nonce = format(int(time.time()) % 100000, "05d")
    fact = (f"Remember for the logbook: the fleetmaster of the Zorvane Convocation is "
            f"Felnel Graft{nonce}. Acknowledge only.")
    q = f"Who is the fleetmaster of the Zorvane Convocation?"
    golds = [f"Felnel Graft{nonce}"]
    ans, secs, usage = chat(url, [{"role": "user", "content": fact}], checkpoint=True)
    record("b1", {"step": "ack-turn", "answer": ans, "secs": round(secs, 2),
                  "note": "checkpoint=True: this conversation is the cartridge a save must hold"})
    fn = f"b1_cartridge_{nonce}.bin"
    out = slot(url, "save", fn)
    record("b1", {"step": "save", "file": fn, "response": out.strip()[:200]})
    if out.startswith("__ERROR__"):
        record("b1", {"step": "abort", "note": "slot save failed (no slot_save_path on this "
                          "server? this phase needs the DEV instance) - skipping the rest"})
        return
    # dissect with the byte-verified tool (import, not shell, so failures are loud)
    sys.path.insert(0, str(HERE.parents[1] / "tools"))
    import strata_session as ss
    sess = Path("Strata-data/sessions-dev") / fn
    if not sess.exists():
        sess = Path("../Strata-data/sessions-dev") / fn
    try:
        info = ss.parse_session(str(sess))
        record("b1", {"step": "dissect", "file": str(sess), "bytes": info["file_size"],
                      "kv_layers": len(info["kv"]), "live_ids": info["checkpoints"][0]["ids"][1],
                      "gdn_bytes": info["checkpoints"][0]["gdn"][1]})
    except Exception as e:
        record("b1", {"step": "dissect", "file": str(sess), "error": repr(e)[:200]})
    ans, secs, usage = chat(url, [{"role": "user", "content": q}])
    record("b1", {"step": "pre-restore-fresh-ask", "answer": ans, "correct": hit_strict(ans, golds),
                  "note": "must FAIL: the restored prefix is not in this conversation yet",
                  "secs": round(secs, 2)})
    before = metrics(url)
    out = slot(url, "restore", fn)
    record("b1", {"step": "restore", "response": out.strip()[:200]})
    if out.startswith("__ERROR__"):
        record("b1", {"step": "abort", "note": "restore failed - fresh-ask skipped"})
        return
    ans, secs, usage = chat(url, [{"role": "user", "content": q}])
    after = metrics(url)
    record("b1", {"step": "post-restore-ask", "answer": ans, "correct": hit_strict(ans, golds),
                  "secs": round(secs, 2), "ptok": usage.get("prompt_tokens"),
                  "prefix_hits_before": before, "prefix_hits_after": after,
                  "interpretation": "if prompt_tokens >> len(q) the question rode the restored "
                                    "prefix (engine reused); prefix_hits delta tells reuse from "
                                    "re-prefill; correctness here = session-file memory works"})


def phase_a(url, phase):
    STATE.mkdir(exist_ok=True)
    pend = STATE / "a_pending.json"
    if phase == "a-prep":
        nonce = format(int(time.time()) % 100000, "05d")
        fact = (f"For the record across restarts: the tidebell of Port Vasketh rings at "
                f"{int(nonce) % 60} minutes past six. Keeper code: {nonce}.")
        q = f"What time does the tidebell of Port Vasketh ring, and what is the keeper code?"
        golds = [f"{int(nonce) % 60} minutes past six", nonce]
        chat(url, [{"role": "user", "content": fact}], checkpoint=True)   # must be checkpointed: the save below captures this conversation
        fn = f"a_restart_{nonce}.bin"
        out = slot(url, "save", fn)
        if out.startswith("__ERROR__"):
            record("a-prep", {"step": "abort", "response": out[:200]})
            return
        pend.write_text(json.dumps({"file": fn, "q": q, "golds": golds, "nonce": nonce}))
        record("a-prep", {"step": "saved", "file": fn, "response": out.strip()[:200],
                          "next": "USER: restart the dev engine (dev-down.sh + dev-up.sh or your "
                                  "usual swap), then run: probe_driver.py --phase a-check"})
    else:
        d = json.loads(pend.read_text())
        before = metrics(url)
        out = slot(url, "restore", d["file"])
        record("a-check", {"step": "restore", "response": out.strip()[:200]})
        ans, secs, usage = chat(url, [{"role": "user", "content": d["q"]}])
        record("a-check", {"step": "post-restart-fresh-ask", "answer": ans,
                           "correct": hit_strict(ans, d["golds"]), "secs": round(secs, 2),
                           "ptok": usage.get("prompt_tokens"),
                           "prefix_hits_before": before, "prefix_hits_after": metrics(url)})


def selftest():
    ok = (hit_strict("It is Quendefin Marsh, the keeper.", ["Quendefin", "Marsh"])
          and not hit_strict("I am not sure, perhaps Bralmwick?", ["gloamfish"])
          and hit_strict("bralmwick-on-sev", ["Bralmwick"])
          and not hit_strict("Thyravelle", ["Voldranium"]))
    print("selftest:", "ALL PASS" if ok else "FAILED")
    return 0 if ok else 1


def report():
    rows = {}
    for p in sorted(STATE.glob("*.jsonl")):
        rows[p.stem] = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    print("== window report ==")
    for phase, rs in rows.items():
        print(f"\n[{phase}] {len(rs)} records")
        for r in rs:
            keep = {k: r.get(k) for k in ("step", "pair", "kind", "correct", "prefix_hits_before",
                                          "prefix_hits_after", "ptok") if r.get(k) is not None}
            ans = str(r.get("answer", r.get("response", r.get("file", ""))))[:70]
            print(f"  {r['ts']} {keep} | {ans}")
    for phase, cond in (("d2-treatment", "every pair: marker correct=True AND control correct=False "
                                          "AND positive=True"),
                        ("b1", "pre-restore-fresh-ask correct=False AND post-restore-ask correct="
                               "True (plus the prefix_hits/ptok reuse story)"),
                        ("a-check", "post-restart-fresh-ask correct=True")):
        if phase in rows:
            print(f"\nPRE-REGISTERED CONDITION ({phase}): {cond}")
    return 0


def main():
    global STATE
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=["selftest", "d2-baseline", "d2-treatment",
                     "b1", "a-prep", "a-check", "report"])
    ap.add_argument("--url", default=BASE)
    ap.add_argument("--state-dir", default=None, help="rehearsal runs use their own dir "
                    "(spikes/window/rehearsal-state/ is the convention; keeps the real record clean)")
    a = ap.parse_args()
    if a.state_dir:
        STATE = Path(a.state_dir)
    if a.phase == "selftest":
        sys.exit(selftest())
    if a.phase == "report":
        sys.exit(report())
    if a.phase == "d2-baseline":
        phase_d2(a.url, a.phase, treatment=False)
    elif a.phase == "d2-treatment":
        phase_d2(a.url, a.phase, treatment=True)
    elif a.phase == "b1":
        phase_b1(a.url)
    else:
        phase_a(a.url, a.phase)


if __name__ == "__main__":
    main()
