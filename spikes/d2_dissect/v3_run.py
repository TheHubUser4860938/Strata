#!/usr/bin/env python3
"""spikes/d2_dissect/v3_run.py - the v3 venue runner (bands PRE-REGISTERED in V3_PRE_REG.md -
read it first; it was written before the copy existed and is not editable after the run).

Venues (--venue):
  steer   the steer target cue + its two never-written control cues + the sanity cue (recorded);
  marker  the three marker cue asks (cue-last, greedy channel) + three control cues;
  plumb   the three fact-in-prompt positives (the read-path gate every verdict needs);
  all     everything, one verdict (default).
  --no-verdict just runs and counts (this is how the COLD BASELINE is taken: --venue steer on a
  boot whose table does NOT hold the v3 writes - the gates proved these cues read zero foreign
  rows, so any boot that is not the v3 copy gives the stock behavior).

Channel (fixed by the pre-registration): /v1, reasoning_effort off, temperature 0.8, max_tokens
24, X-Strata-Memory: off on EVERY ask, one fresh conversation per ask, 3 passes. Geometry guard:
usage.prompt_tokens must equal the local venue_render render or the ask is VOID (v2's rule -
the memory-eval store is live on this boot and would silently prepend ~300 tokens).

Scoring: steer hit = 'year' as a word (controls are the defense: any control 'year' anywhere =
leak = VOID); marker gold = own pair's expect substring; control leak = any bank gold string in a
control answer; plumbing must be 9/9 or the whole run is VOID. Verdict matrix: V3_PRE_REG.md.
Robustness (audit 2026-10-11): HTTP/transport failures are RECORDED per ask (a transient 503
must not crash a single-use boot's run before the verdict file exists); the boot-mount evidence
line ("PLE on, table ...", engine stderr = strata-dev.log) is captured before scoring and
recorded in the verdict file, as V3_PRE_REG §Venue requires.

Usage: python3 spikes/d2_dissect/v3_run.py [--url http://127.0.0.1:8005] [--venue all]
           [--temp 0.8] [--repeats 3] [--tag NAME] [--no-verdict] [--note TEXT] [--dry-run]
Writes state/v3_run.jsonl (append) + state/v3_verdict_<tag>.txt.
"""
from __future__ import annotations
import argparse, datetime, json, re, sys, time, urllib.error, urllib.request as U
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "spikes" / "window"))

from venue_render import load_tokenizer, load_template, live_render  # noqa: E402
from build_d2_plan_v2 import CUE_PREAMBLE  # noqa: E402
from build_d2_plan_v3 import STEER_CUES  # noqa: E402
from v3_gates import SANITY_CUE  # noqa: E402

YEAR_RE = re.compile(r"(?<!\w)year")
MARKERS = ("d2-archivist", "d2-metalsmith", "d2-recipe")


def http(url, payload=None, headers=None, timeout=960):
    """-> (status, parsed_body). A 4xx/5xx (503 engine starting/stuck, 409 ModelBusy, ...) or a
    transport error is RETURNED, not raised - one transient failure must not crash the run
    before the verdict file is written (audit 2026-10-11). 960 s covers the worst-case
    in-request engine reload (ENGINE_READY_S=900, server.py:265; idle_unload_s=700 reachable)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = U.Request(url, data=data, method="POST" if data is not None else "GET")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with U.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}
    except OSError as e:  # URLError/socket.timeout/connection-reset are OSError subclasses
        return 0, {"raw": f"transport error: {e}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--venue", default="all", choices=("all", "steer", "marker", "plumb"))
    ap.add_argument("--temp", type=float, default=0.8)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--tag", default=datetime.datetime.now().strftime("%H%M%S"))
    ap.add_argument("--maxtok", type=int, default=24)
    ap.add_argument("--no-verdict", action="store_true")
    ap.add_argument("--note", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    url = a.url.rstrip("/")
    a.tag = re.sub(r"[^A-Za-z0-9_-]", "_", a.tag)  # the tag lands in output filenames (c2 rule)
    if a.repeats < 3:
        raise SystemExit("--repeats >= 3 is mandatory protocol (V3_PRE_REG.md: >= 3 passes)")

    tok = load_tokenizer()
    tpl = load_template()
    mk = json.loads((ROOT / "spikes/window/markers.json").read_text())
    cues = json.loads((HERE / "state/v3_build.json").read_text())["marker_cue"]
    pair = {p["name"]: p for p in mk["pairs"]}
    all_golds = sorted({e.lower() for p in mk["pairs"] for e in p["expect"]})

    asks = []
    if a.venue in ("all", "steer"):
        asks.append({"kind": "steer", "name": "steer-target",
                     "text": f"{CUE_PREAMBLE} {STEER_CUES['steer-target']}", "want": ["year"]})
        for nm in ("steer-ctrl-1", "steer-ctrl-2"):
            asks.append({"kind": "steer_ctrl", "name": nm,
                         "text": f"{CUE_PREAMBLE} {STEER_CUES[nm]}", "want": []})
        asks.append({"kind": "sanity", "name": "ordinary-english",
                     "text": f"{CUE_PREAMBLE} {SANITY_CUE}", "want": []})
    if a.venue in ("all", "marker"):
        for nm in MARKERS:
            asks.append({"kind": "marker", "name": nm, "text": f"{CUE_PREAMBLE} {cues[nm]}",
                         "want": [e.lower() for e in pair[nm]["expect"]]})
            asks.append({"kind": "control", "name": nm,
                         "text": f"{CUE_PREAMBLE} {pair[nm]['control_marker']}", "want": []})
    if a.venue in ("all", "plumb"):
        for nm in MARKERS:
            asks.append({"kind": "positive", "name": nm,
                         "text": f"{pair[nm]['fact_text']} Given only that, answer.\n\n"
                                 f"{CUE_PREAMBLE} {cues[nm]}",
                         "want": [e.lower() for e in pair[nm]["expect"]]})

    for q in asks:
        ids, _ = live_render(tok, tpl, [{"role": "user", "content": q["text"]}], "off")
        q["expect_ptok"] = len(ids)
    if a.dry_run:
        print(json.dumps([{k: v for k, v in q.items() if k != "text"} | {"ask": q["text"]}
                          for q in asks], indent=1))
        return 0

    st, health = http(url + "/health")
    if st != 200:
        raise SystemExit(f"/health -> {st}: {json.dumps(health)[:160]} - no run")
    model = health.get("model") or "strata"
    # V3_PRE_REG §Venue: the boot-mount evidence ("PLE on, table ... d2v3") is engine stderr
    # (generate.cpp:4267 -> strata-dev.log), captured HERE - before any scoring - and recorded.
    ple_line = ""
    try:
        for ln in reversed((ROOT / "strata-dev.log").read_bytes()
                           .decode("utf-8", "replace").splitlines()):
            if "PLE on, table" in ln:
                ple_line = ln.strip()[:200]
                break
    except OSError:
        pass
    print(f"health {health.get('status')} model={model} venue={a.venue} tag={a.tag} "
          f"channel=reasoning_effort=off,temperature={a.temp},memory-muted repeats={a.repeats}")
    print(f"boot evidence: {ple_line or 'NO \"PLE on, table\" line found in strata-dev.log'}")
    out_jsonl = HERE / "state/v3_run.jsonl"
    (HERE / "state").mkdir(exist_ok=True)
    recs = []
    for rep in range(a.repeats):
        for q in asks:
            body = {"model": model, "messages": [{"role": "user", "content": q["text"]}],
                    "temperature": a.temp, "reasoning_effort": "off",
                    "max_tokens": a.maxtok, "stream": False}
            t0 = time.time()
            st, r = http(url + "/v1/chat/completions", body, headers={"X-Strata-Memory": "off"})
            if st != 200:  # recorded per-ask and continued: the run survives to the verdict
                ans = f"HTTP {st}: {json.dumps(r)[:110]}"
            else:
                ans = (((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            ptok = (r.get("usage") or {}).get("prompt_tokens")
            low = ans.lower()
            rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                   "tag": a.tag, "venue": a.venue, "pass": rep + 1, "kind": q["kind"],
                   "name": q["name"], "answer": ans, "ptok": ptok,
                   "expect_ptok": q["expect_ptok"], "geom_ok": ptok == q["expect_ptok"],
                   "year_hit": bool(YEAR_RE.search(low)) if q["kind"] in ("steer", "steer_ctrl") else None,
                   "gold_hit": any(w in low for w in q["want"]) if q["want"] else None,
                   "leak": ((("year" in low) if q["kind"] == "steer_ctrl" else   # PRE_REG: "anywhere"
                             any(g in low for g in all_golds))
                            if q["kind"] in ("steer_ctrl", "control") else False),
                   "channel": f"off/temp{a.temp}", "memory_muted": "X-Strata-Memory: off",
                   "secs": round(time.time() - t0, 2)}
            recs.append(rec)
            with out_jsonl.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            flag = ("LEAK" if rec["leak"] else
                    "HIT" if rec["year_hit"] or rec["gold_hit"] else
                    "recorded" if q["kind"] == "sanity" else "miss")
            print(f"[p{rep + 1}/{rec['kind']}] {rec['name']}: ptok {ptok}/{q['expect_ptok']} "
                  f"{'OK' if rec['geom_ok'] else 'MISMATCH-VOID'} -> {ans[:70]!r} {flag}")

    # ---- verdict, mechanically per V3_PRE_REG.md --------------------------------------------
    # Geometry is a PER-ASK VOID here (V3_PRE_REG:26-27: "or the ask is VOID" - unlike C2's
    # run-level rule; audit finding 12: the code used to void the WHOLE run on one mismatch).
    # Bands compute over the surviving records; a VOIDed plumbing ask simply cannot reach 9/9.
    def scored(kind=None):
        return [r for r in recs if r["geom_ok"] and (kind is None or r["kind"] == kind)]
    n_geom_bad = sum(1 for r in recs if not r["geom_ok"])
    pos = scored("positive")
    plumb_gold = sum(1 for r in pos if r["gold_hit"])
    plumb_ok = len(pos) == 3 * a.repeats and plumb_gold == len(pos)
    leaks = [r for r in scored() if r["leak"]]
    tgt = scored("steer")
    ctrl = scored("steer_ctrl")
    tgt_hits = sum(1 for r in tgt if r["year_hit"])
    tgt_by_pass = {p: sum(1 for r in tgt if r["pass"] == p and r["year_hit"])
                   for p in range(1, a.repeats + 1)}
    ctrl_year = sum(1 for r in ctrl if r["year_hit"])
    mkr = scored("marker")
    mkr_by_pass = {p: sum(1 for r in mkr if r["pass"] == p and r["gold_hit"])
                   for p in range(1, a.repeats + 1)}
    mkr_gold = sum(1 for r in mkr if r["gold_hit"])

    def band():
        note_geom = (f"{n_geom_bad} ask(s) VOID by the geometry guard (per-ask rule); "
                     if n_geom_bad else "")
        if leaks:
            return ("VOID - control leak: " +
                    str([{"kind": x["kind"], "name": x["name"], "answer": x["answer"][:60]}
                         for x in leaks]))
        if a.venue in ("all", "plumb") and not plumb_ok:
            return (f"VOID - plumbing gate {plumb_gold}/{len(pos)} (9/9 registered; VOIDed asks "
                    "count against the 9): this boot cannot support any v3 verdict")
        if a.venue != "all" or a.no_verdict:
            return (note_geom + f"PARTIAL RUN (venue={a.venue}, no-verdict={a.no_verdict}) - "
                    f"counts only: steer {tgt_hits}/{len(tgt)} per-pass {tgt_by_pass}, "
                    f"steer-ctrl 'year' {ctrl_year}/{len(ctrl)}, markers {mkr_gold}/{len(mkr)} "
                    f"per-pass {mkr_by_pass}, plumbing {plumb_gold}/{len(pos)}")
        # PRE_REG bands: STEERS = "target in >= 2 of 3 passes" (one target ask per pass, so
        # count QUALIFYING PASSES); markers ">= 2/3 in >= 2 passes" - TWO qualifying passes, the
        # original any() accepted one lucky pass (audit finding 8).
        steer_passes = sum(1 for v in tgt_by_pass.values() if v >= 1)
        steers = steer_passes >= 2
        mk_live = sum(1 for v in mkr_by_pass.values() if v >= 2) >= 2
        inert = tgt_hits == 0 and ctrl_year == 0 and mkr_gold == 0
        mk_dead = mkr_gold == 0
        if steers:
            if mk_live:
                return ("TIER-1 ALIVE via the emit-window recipe (H6 confirmed): steer "
                        f"{tgt_hits}/{len(tgt)} per-pass {tgt_by_pass}, markers {mkr_gold}/"
                        f"{len(mkr)} per-pass {mkr_by_pass} - design v4 around emit-pre srcs")
            if mk_dead:
                return ("TARGET-SENSITIVE - steering is real (steer "
                        f"{tgt_hits}/{len(tgt)} per-pass {tgt_by_pass}) but the emit-pre target "
                        "still misses markers 0/" + str(len(mkr)) + ": one more offline "
                        "src-candidate round (name-run rows, wider tap context) before any new copy")
            return (f"MECHANISM-SENSITIVE - steer {tgt_hits}/{len(tgt)} per-pass {tgt_by_pass}, "
                    f"markers {mkr_gold}/{len(mkr)} per-pass {mkr_by_pass}: between bands")
        if inert:
            return ("INERT - table-row writes are inert as a readout at this dosage on this "
                    f"architecture (H7): steer 0/{len(tgt)}, controls 0/{len(ctrl)} 'year', "
                    f"markers 0/{len(mkr)}, plumbing green - tier-1 closes, the cartridge tier "
                    "owns numerical memory")
        return ("MECHANISM-SENSITIVE - "
                f"steer {tgt_hits}/{len(tgt)} per-pass {tgt_by_pass}, ctrl 'year' {ctrl_year}/"
                f"{len(ctrl)}, markers {mkr_gold}/{len(mkr)} per-pass {mkr_by_pass}: recorded, "
                "stop, no venue-hunting without a new pre-registration")

    verdict = band()
    tail = (f"\nv3 verdict @{datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')} "
            f"tag={a.tag} venue={a.venue} channel=off/temp{a.temp} repeats={a.repeats}\n"
            f"  boot evidence: {ple_line or 'NOT FOUND in strata-dev.log'}\n"
            f"  steer {tgt_hits}/{len(tgt)} per-pass {tgt_by_pass} | steer-ctrl 'year' "
            f"{ctrl_year}/{len(ctrl)} | markers {mkr_gold}/{len(mkr)} per-pass {mkr_by_pass} | "
            f"plumbing {plumb_gold}/{len(pos)} | leaks {len(leaks)} | geometry "
            f"{len(recs) - n_geom_bad}/{len(recs)} scored ({n_geom_bad} per-ask VOID) | sanity "
            f"{[r['answer'][:40] for r in recs if r['kind'] == 'sanity'][:1]}\n"
            f"  {verdict}\n")
    if a.note:
        tail += "note: " + a.note.strip() + "\n"
    print(tail)
    (HERE / f"state/v3_verdict_{a.tag}.txt").write_text(
        "\n".join(f"[{r['tag']}/p{r['pass']}/{r['kind']}] {r['name']} ptok={r['ptok']}/"
                  f"{r['expect_ptok']} year={r['year_hit']} gold={r['gold_hit']} "
                  f"leak={r['leak']} :: {r['answer']}" for r in recs) + tail)
    return 1 if verdict.startswith("VOID") else 0  # scripting can gate on the exit code too


if __name__ == "__main__":
    sys.exit(main())
