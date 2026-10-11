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
    ap.add_argument("--venue", default="A", choices=["A", "C", "HIGH"],
                    help="A = authoritative greedy cue-asks (pre-registered); "
                         "C = plumbing discriminators (fact given in-prompt, greedy); "
                         "HIGH = the same cues through the WEBAPP channel defaults "
                         "(reasoning_effort high, temperature 0.6) - realism, scored on 'content' "
                         "only, reasoning comes back separate")
    ap.add_argument("--note", default="", help="appended to the verdict file verbatim")
    ap.add_argument("--temp", type=float, default=None,
                    help="sampling temperature override (user directive 2026-10-11: run at 0.8 "
                         "or 1.0, not 0 - temp 0 on this build is not bit-stable across "
                         "expert-cache states, so a cold 'idk' at 0 could be a decoding "
                         "artifact). With temp > 0 the answers are SAMPLED: use --repeats.")
    ap.add_argument("--repeats", type=int, default=1, help="pass the whole ask set N times")
    a = ap.parse_args()

    tok = load_tokenizer()
    tpl = load_template()
    mk = json.loads((ROOT / "spikes/window/markers.json").read_text())
    cues = json.loads((HERE / "state/v2_build.json").read_text())["cue"]
    pair = {p["name"]: p for p in mk["pairs"]}

    asks = []
    if a.venue == "A":
        for nm in ("d2-archivist", "d2-metalsmith", "d2-recipe"):
            asks.append({"kind": "marker", "name": nm, "text": f"{CUE_PREAMBLE} {cues[nm]}",
                         "want": pair[nm]["expect"]})
            asks.append({"kind": "control", "name": nm,
                         "text": f"{CUE_PREAMBLE} {pair[nm]['control_marker']}",
                         "want": ["idk"]})
        asks.append({"kind": "sanity", "name": "ordinary-english",
                     "text": f"{CUE_PREAMBLE} Glasgow-University principal", "want": []})
    elif a.venue == "C":          # read-path discriminators: the fact is IN the prompt
        for nm in ("d2-archivist", "d2-metalsmith", "d2-recipe"):
            asks.append({"kind": "positive", "name": nm,
                         "text": f"{pair[nm]['fact_text']} Given only that, answer.\n\n"
                                 f"{CUE_PREAMBLE} {cues[nm]}",
                         "want": pair[nm]["expect"]})
    else:                        # HIGH = webapp channel defaults, same cues as A
        for nm in ("d2-archivist", "d2-metalsmith", "d2-recipe"):
            asks.append({"kind": "marker", "name": nm, "text": f"{CUE_PREAMBLE} {cues[nm]}",
                         "want": pair[nm]["expect"]})
            asks.append({"kind": "control", "name": nm,
                         "text": f"{CUE_PREAMBLE} {pair[nm]['control_marker']}",
                         "want": ["idk"]})

    effort, temp, maxtok = (("off", 0, 24) if a.venue in ("A", "C") else ("high", 0.6, 8192))
    if a.temp is not None:
        temp = a.temp
    all_golds = sorted({e.lower() for p in mk["pairs"] for e in p["expect"]})
    for q in asks:
        ids, prompt = live_render(tok, tpl, [{"role": "user", "content": q["text"]}], effort)
        q["expect_ptok"] = len(ids)
        q["tail_tokens"] = [tok.decode([t]) for t in ids[-4:]]
        q["tap_window"] = [tok.decode([ids[-9]]), tok.decode([ids[-10]])]
    if a.dry_run:
        print(json.dumps([{k: v for k, v in q.items() if k != "text"} | {"ask": q["text"]}
                         for q in asks], indent=1))
        return 0

    health = json.loads(http(a.url.rstrip("/") + "/health"))
    print("health:", health.get("status"), "model:", health.get("model"),
          f"venue={a.venue} channel reasoning_effort={effort} temp={temp} repeats={a.repeats}")
    out_jsonl = HERE / "state/v2_run.jsonl"
    recs = []
    for rep in range(a.repeats):
        for q in asks:
            body = {"model": health.get("model") or "strata",
                    "messages": [{"role": "user", "content": q["text"]}],
                    "temperature": temp, "reasoning_effort": effort, "max_tokens": maxtok,
                    "stream": False}
            # the dev boot carries the memory-eval store (100 facts, inject on) - the recalled
            # block would prepend ~310 tokens and the venue would no longer be the prompt whose
            # tap row we wrote (measured 2026-10-11 13:43Z: ptok 361-367 vs 50-56 rendered, the
            # guard VOIDed it). X-Strata-Memory: off mutes injection AND journaling
            # (serve/memory.py request_state), so the mechanism under test is the TABLE alone.
            t0 = time.time()
            r = json.loads(http(a.url.rstrip("/") + "/v1/chat/completions", body,
                                headers={"X-Strata-Memory": "off"}))
            ch = r["choices"][0]["message"]
            ans = (ch.get("content") or "").strip()
            ptok = (r.get("usage") or {}).get("prompt_tokens")
            rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                   "venue": a.venue, "pass": rep + 1,
                   "channel": f"reasoning_effort={effort},temperature={temp}",
                   "kind": q["kind"], "name": q["name"], "ask": q["text"], "answer": ans,
                   "reasoning_len": len(ch.get("reasoning_content") or ""),
                   "ptok": ptok, "expect_ptok": q["expect_ptok"],
                   "prompt_geometry_ok": ptok == q["expect_ptok"],
                   "memory_muted": "X-Strata-Memory: off",
                   "tap_window": q["tap_window"], "secs": round(time.time() - t0, 2)}
            hits = [e for e in q["want"] if e.lower() in ans.lower()]
            rec["want"] = q["want"]
            # control rule, AMENDED 2026-10-11 with the user's webapp evidence: in the thinking
            # channel the model CONFABULATES instead of saying idk (Hermaeus Mora / Gerudo Town /
            # "Wendfallian sausage" for the three controls), so "not idk" is a channel property,
            # not a leak. Contamination (what the pre-registered VOID actually protects against) =
            # a control that produces one of THIS table's gold strings. Both are recorded.
            rec["idk"] = "idk" in ans.lower()
            rec["leak"] = any(g in ans.lower() for g in all_golds)
            rec["truncated"] = (not ans) and rec["reasoning_len"] > 0
            rec["correct"] = (None if (rec["truncated"] or q["kind"] == "sanity") else
                              (bool(hits) if q["kind"] != "control" else rec["idk"]))
            recs.append(rec)
            with out_jsonl.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            if q["kind"] == "sanity":
                label = "recorded (not scored)"
            elif rec["truncated"]:
                label = "TRUNCATED (no answer content)"
            elif q["kind"] == "control":
                label = ("LEAK (gold appeared)" if rec["leak"] else
                         "PASS (idk)" if rec["idk"] else f"confab: {ans[:40]!r}")
            else:
                label = "PASS" if rec["correct"] else "fail"
            print(f"[{rec['venue']}/p{rep + 1}/{rec['kind']}] {rec['name']}: ptok {ptok} "
                  f"(expect {q['expect_ptok']}, {'OK' if rec['prompt_geometry_ok'] else 'MISMATCH'}) "
                  f"-> {ans[:90]!r} {label}")

    geom_ok = all(r["prompt_geometry_ok"] for r in recs)
    trunc = [r for r in recs if r["truncated"]]
    m = [r for r in recs if r["kind"] == "marker" and not r["truncated"]]
    c = [r for r in recs if r["kind"] == "control" and not r["truncated"]]
    leaks = [r for r in c if r["leak"]]
    confab = [r for r in c if not r["idk"] and not r["leak"]]
    mk_pass = sum(1 for r in m if r["correct"])
    by_pass = {}
    for r in m:
        by_pass[r["pass"]] = by_pass.get(r["pass"], 0) + (1 if r["correct"] else 0)
    bands_ok = any(v >= 2 for v in by_pass.values())
    zero_everywhere = bool(by_pass) and all(v == 0 for v in by_pass.values())
    if not geom_ok:
        verdict = ("VOID - prompt geometry mismatch: the live prompt differs from the rendered "
                   "venue, so the tap arithmetic does not apply")
    elif a.venue == "C":
        pos = sum(1 for r in recs if r["correct"])
        verdict = (f"{pos}/{len(recs)} positives answered from an in-prompt fact -> read path "
                   + ("WORKING (a marker miss is a memory miss, not plumbing)" if pos == len(recs)
                      else "BROKEN - this boot cannot support any marker verdict"))
    elif leaks:
        verdict = ("VOID - control contamination: a control produced one of this table's gold "
                   "strings: " + str([x["answer"][:60] for x in leaks]))
    elif not m:
        verdict = f"INCONCLUSIVE - no marker reached an answer (truncated {len(trunc)}/{len(recs)})"
    elif bands_ok:
        verdict = ("D2v2 MARKERS LAND - a pass reached the pre-registered >= 2/3 band (gold "
                   f"rate {mk_pass}/{len(m)} over {len(by_pass)} pass(es) at temp {temp})")
    elif zero_everywhere:
        verdict = (f"D2v2 READOUT MISS - gold {mk_pass}/{len(m)} over {len(by_pass)} pass(es) at "
                   f"temp {effort and ''}{temp}; controls idk {sum(1 for r in c if r['idk'])}/"
                   f"{len(c)}" + (f", {len(confab)} confabulated (channel property, not a leak)"
                                  if confab else "") +
                   " - the written gold never surfaces in the geometrically-reachable venue")
    else:
        verdict = (f"MECHANISM-SENSITIVE - gold rate {mk_pass}/{len(m)} at temp {temp}: per-pass "
                   f"{by_pass} sits between the pre-registered bands")
    tail = (f"\nD2v2 Venue {a.venue} verdict @ reasoning_effort={effort}, temperature={temp}: "
            f"markers {mk_pass}/{len(m)} (per pass {by_pass}), controls idk "
            f"{sum(1 for r in c if r['idk'])}/{len(c)}, confabulated {len(confab)}, leaked "
            f"{len(leaks)}, truncated {len(trunc)}, geometry {'all-OK' if geom_ok else 'BROKEN'}"
            f"\n{verdict}\n")
    if a.note:
        tail += "note: " + a.note.strip() + "\n"
    print(tail)
    (HERE / f"state/v2_run_verdict_{a.venue}.txt").write_text(
        "generated " + recs[0]["ts"] + f" channel reasoning_effort={effort},temperature={temp}"
        f" repeats={a.repeats}\n" + "\n".join(
            f"[p{r['pass']}/{r['kind']}] {r['name']} ptok={r['ptok']}/{r['expect_ptok']} "
            f"{'RECORDED (not scored)' if r['kind'] == 'sanity' else 'TRUNCATED' if r['truncated'] else ('LEAK' if r['leak'] else ('PASS' if r['correct'] else 'fail'))}"
            f" :: {r['answer']}" for r in recs) + tail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
