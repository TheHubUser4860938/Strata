#!/usr/bin/env python3
"""spikes/d2_dissect/v2_run.py - Venue A of PASTE-V2.md: the authoritative D2v2 measurement,
scripted (no paste), against the dev server on the PATCHED v2 venue.

Channel (the one the b1/a-check runs proved): POST /v1/chat/completions, ONE fresh conversation
per ask, temperature 0, reasoning_effort "off" -> greedy decode, no thinking block, generation
tail = the 9 ids whose arithmetic this plan was built on.

Venue sanity per ask (this is what makes a PASS mean something): the local render of the same
ask (venue_render, pack tokenizer + pack template) predicts the exact prompt size; the run is
only scored when usage.prompt_tokens EQUALS that number - i.e. the engine really read the prompt
whose tap position n-9 we wrote gold onto.

Verdict text is PRE-REGISTERED in PASTE-V2.md (written before any measurement, do not edit
after the run): markers >= 2/3 AND controls 0/3 (all idk) = D2v2 MARKERS LAND; markers 0/3 =
the residual-carry route is dead as well; any control not-idk = control contaminated = VOID.
Item 7 (an ordinary English bigram) is recorded, never scored.

Usage: python3 spikes/d2_dissect/v2_run.py [--url http://127.0.0.1:8005] [--dry-run]
  --dry-run renders + prints expected prompt sizes + the ask texts and contacts nothing.
Writes state/v2_run.jsonl (append) + state/v2_run_verdict.txt.
"""
from __future__ import annotations
import argparse, datetime, json, sys, time, urllib.request as U
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from venue_render import load_tokenizer, load_template, live_render  # noqa: E402
from build_d2_plan_v2 import CUE_PREAMBLE  # noqa: E402


def http(url, payload=None, headers=None, method=None, timeout=600):
    data = json.dumps(payload).encode() if payload is not None else None
    req = U.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with U.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    tok = load_tokenizer()
    tpl = load_template()
    mk = json.loads((ROOT / "spikes/window/markers.json").read_text())
    cues = json.loads((HERE / "state/v2_build.json").read_text())["cue"]
    pair = {p["name"]: p for p in mk["pairs"]}

    asks = []
    for nm in ("d2-archivist", "d2-metalsmith", "d2-recipe"):
        asks.append({"kind": "marker", "name": nm, "text": f"{CUE_PREAMBLE} {cues[nm]}",
                     "want": pair[nm]["expect"]})
        asks.append({"kind": "control", "name": nm,
                     "text": f"{CUE_PREAMBLE} {pair[nm]['control_marker']}",
                     "want": ["idk"]})
    asks.append({"kind": "sanity", "name": "ordinary-english",
                 "text": f"{CUE_PREAMBLE} Glasgow-University principal", "want": []})

    for q in asks:
        ids, prompt = live_render(tok, tpl, [{"role": "user", "content": q["text"]}], "off")
        q["expect_ptok"] = len(ids)
        q["tail_tokens"] = [tok.decode([t]) for t in ids[-4:]]
        q["tap_window"] = [tok.decode([ids[-9]]), tok.decode([ids[-10]])]
    if a.dry_run:
        print(json.dumps([{k: v for k, v in q.items() if k != "text"} | {"ask": q["text"]}
                         for q in asks], indent=1))
        return 0

    health = json.loads(http(a.url.rstrip("/") + "/health"))
    print("health:", health.get("status"), "model:", health.get("model"))
    out_jsonl = HERE / "state/v2_run.jsonl"
    recs = []
    for q in asks:
        body = {"model": health.get("model") or "strata", "messages": [{"role": "user", "content": q["text"]}],
                "temperature": 0, "reasoning_effort": "off", "max_tokens": 24, "stream": False}
        t0 = time.time()
        r = json.loads(http(a.url.rstrip("/") + "/v1/chat/completions", body))
        ch = r["choices"][0]["message"]
        ans = (ch.get("content") or "").strip()
        ptok = (r.get("usage") or {}).get("prompt_tokens")
        rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "kind": q["kind"], "name": q["name"], "ask": q["text"], "answer": ans,
               "ptok": ptok, "expect_ptok": q["expect_ptok"], "prompt_geometry_ok": ptok == q["expect_ptok"],
               "tap_window": q["tap_window"], "secs": round(time.time() - t0, 2)}
        hits = [e for e in q["want"] if e.lower() in ans.lower()]
        rec["want"] = q["want"]
        rec["correct"] = bool(hits) if q["kind"] != "control" else ("idk" in ans.lower())
        recs.append(rec)
        with out_jsonl.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"[{rec['kind']}] {rec['name']}: ptok {ptok} (expect {q['expect_ptok']}, "
              f"{'OK' if rec['prompt_geometry_ok'] else 'MISMATCH'}) -> {ans[:90]!r} "
              f"{'PASS' if rec['correct'] else 'fail'}")

    geom_ok = all(r["prompt_geometry_ok"] for r in recs)
    m = [r for r in recs if r["kind"] == "marker"]
    c = [r for r in recs if r["kind"] == "control"]
    mk_pass, ct_pass = sum(r["correct"] for r in m), sum(r["correct"] for r in c)
    if not geom_ok:
        verdict = ("VOID - prompt geometry mismatch: the live prompt differs from the rendered "
                   "venue, so the tap arithmetic does not apply")
    elif ct_pass != 3:
        verdict = "VOID - control contamination (a control read a written row or answered like one)"
    elif mk_pass >= 2:
        verdict = "D2v2 MARKERS LAND - the written gold surfaces cold in the cue channel"
    elif mk_pass == 0:
        verdict = ("D2v2 READOUT MISS AGAIN, in the geometrically-reachable venue - the "
                   "residual-carry route is dead too; PLE writes need a different mechanism")
    else:
        verdict = f"MECHANISM-SENSITIVE - markers {mk_pass}/3 (between the pre-registered bands)"
    tail = (f"\nD2v2 Venue A verdict: markers {mk_pass}/3, controls {ct_pass}/3 (want 3), "
            f"geometry {'all-OK' if geom_ok else 'BROKEN'}\n{verdict}\n")
    print(tail)
    (HERE / "state/v2_run_verdict.txt").write_text(
        "generated " + recs[0]["ts"] + "\n" + "\n".join(
            f"[{r['kind']}] {r['name']} ptok={r['ptok']}/{r['expect_ptok']} "
            f"{'PASS' if r['correct'] else 'fail'} :: {r['answer']}" for r in recs) + tail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
