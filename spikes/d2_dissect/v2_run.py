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
          f"venue={a.venue} channel reasoning_effort={effort} temp={temp}")
    out_jsonl = HERE / "state/v2_run.jsonl"
    recs = []
    for q in asks:
        body = {"model": health.get("model") or "strata", "messages": [{"role": "user", "content": q["text"]}],
                "temperature": temp, "reasoning_effort": effort, "max_tokens": maxtok, "stream": False}
        # the dev boot carries the memory-eval store (100 facts, inject on) - the recalled block
        # would prepend ~310 tokens and the venue would no longer be the prompt whose tap row we
        # wrote (measured 2026-10-11 13:43Z: ptok 361-367 vs 50-56 rendered, guard VOIDed it).
        # X-Strata-Memory: off mutes injection AND journaling (serve/memory.py request_state), so
        # the mechanism under test is the TABLE alone.
        t0 = time.time()
        r = json.loads(http(a.url.rstrip("/") + "/v1/chat/completions", body,
                            headers={"X-Strata-Memory": "off"}))
        ch = r["choices"][0]["message"]
        ans = (ch.get("content") or "").strip()
        ptok = (r.get("usage") or {}).get("prompt_tokens")
        rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "venue": a.venue, "channel": f"reasoning_effort={effort},temperature={temp}",
               "kind": q["kind"], "name": q["name"], "ask": q["text"], "answer": ans,
               "reasoning_len": len(ch.get("reasoning_content") or ""),
               "ptok": ptok, "expect_ptok": q["expect_ptok"], "prompt_geometry_ok": ptok == q["expect_ptok"],
               "memory_muted": "X-Strata-Memory: off",
               "tap_window": q["tap_window"], "secs": round(time.time() - t0, 2)}
        hits = [e for e in q["want"] if e.lower() in ans.lower()]
        rec["want"] = q["want"]
        # an empty content with a long reasoning block = the thinking ate the whole budget
        # (measured 2026-10-11 13:48Z: reasoning_len 2902 at max_tokens 900) - that is an
        # INCONCLUSIVE reply, never a miss, and it must not enter the verdict counts.
        rec["truncated"] = (not ans) and rec["reasoning_len"] > 0
        rec["correct"] = (None if (rec["truncated"] or q["kind"] == "sanity") else
                          (bool(hits) if q["kind"] != "control" else ("idk" in ans.lower())))
        recs.append(rec)
        with out_jsonl.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        if q["kind"] == "sanity":
            label = "recorded (not scored)"
        elif rec["truncated"]:
            label = "TRUNCATED (no answer content)"
        else:
            label = "PASS" if rec["correct"] else "fail"
        print(f"[{rec['venue']}/{rec['kind']}] {rec['name']}: ptok {ptok} (expect {q['expect_ptok']}, "
              f"{'OK' if rec['prompt_geometry_ok'] else 'MISMATCH'}) -> {ans[:90]!r} {label}")

    geom_ok = all(r["prompt_geometry_ok"] for r in recs)
    trunc = [r for r in recs if r["truncated"]]
    m = [r for r in recs if r["kind"] == "marker" and not r["truncated"]]
    c = [r for r in recs if r["kind"] == "control" and not r["truncated"]]
    mk_pass, ct_pass = sum(1 for r in m if r["correct"]), sum(1 for r in c if r["correct"])
    if not geom_ok:
        verdict = ("VOID - prompt geometry mismatch: the live prompt differs from the rendered "
                   "venue, so the tap arithmetic does not apply")
    elif a.venue == "C":
        pos = sum(1 for r in recs if r["correct"])
        verdict = (f"{pos}/{len(recs)} positives answered from an in-prompt fact -> read path "
                   + ("WORKING (a marker miss is a memory miss, not plumbing)" if pos == len(recs)
                      else "BROKEN - this boot cannot support any marker verdict"))
    elif trunc:
        verdict = (f"INCONCLUSIVE - {len(trunc)}/{len(recs)} asks never reached an answer "
                   f"(the thinking block ate max_tokens); markers {mk_pass}/{len(m)} answered")
    elif ct_pass != len(c):
        verdict = "VOID - control contamination (a control read a written row or answered like one)"
    elif mk_pass >= 2:
        verdict = "D2v2 MARKERS LAND - the written gold surfaces cold in the cue channel"
    elif mk_pass == 0:
        verdict = ("D2v2 READOUT MISS AGAIN, in the geometrically-reachable venue - the "
                   "residual-carry route is dead too; PLE writes need a different mechanism")
    else:
        verdict = f"MECHANISM-SENSITIVE - markers {mk_pass}/3 (between the pre-registered bands)"
    tail = (f"\nD2v2 Venue {a.venue} verdict: markers {mk_pass}/{len(m) or 3}, controls "
            f"{ct_pass}/{len(c) or 3} (want all), truncated {len(trunc)}, "
            f"geometry {'all-OK' if geom_ok else 'BROKEN'}\n{verdict}\n")
    if a.note:
        tail += "note: " + a.note.strip() + "\n"
    print(tail)
    (HERE / f"state/v2_run_verdict_{a.venue}.txt").write_text(
        "generated " + recs[0]["ts"] + " channel " + f"reasoning_effort={effort},temperature={temp}" +
        "\n" + "\n".join(
            f"[{r['kind']}] {r['name']} ptok={r['ptok']}/{r['expect_ptok']} "
            f"{'RECORDED (not scored)' if r['kind'] == 'sanity' else 'TRUNCATED' if r['truncated'] else ('PASS' if r['correct'] else 'fail')}"
            f" :: {r['answer']}" for r in recs) + tail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
