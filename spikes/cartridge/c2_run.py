#!/usr/bin/env python3
"""spikes/cartridge/c2_run.py - the tier-2 C2 verdict driver (venue + bands PRE-REGISTERED in
C2_PRE_REG.md - read that first; do not edit it after this runs).

Shape (per the pre-registration; channel reasoning_effort off + temperature 0.8 + memory muted on
EVERY request, X-Strata-Memory: off - the injected block would break the token-exact prefix
identity the ride guard measures):

  per pass (3) x per marker (3 D2-v2 synthetic pairs):
    encode  = fresh conversation, the 3 invented facts as three user messages, each acked by the
              model (ack <= 16 tokens, replayed verbatim thereafter);
    save    -> n_saved (the engine's own bank-token count = the guard's ground truth);
    C2a ask = question continues the encode conversation in the live engine (recall alone, no
              restore in the loop); ride guard: cache_n >= n_saved - slack, prompt_n <= tail + slack;
    C2b ask = restore the bank file, send the IDENTICAL message list + question (the product
              shape); same guards against n_restored + capture of the engine's per-request
              reuse-summary line from strata-dev.log as corroboration (stderr; dated PRE_REG
              amendment 2026-10-11);
    controls = cold (question only), plumbing (fact + question in one prompt - the read-path
              gate), decoy-real (unrelated bank installed, real cue - leak discriminator),
              decoy-own (unrelated bank, its own cue - recorded bonus, never scored).

Scoring classes: gold > distractor (another pair's gold - MAM's documented failure mode) >
cue-echo > idk > confab. Verdict bands are in the pre-registration; the driver refuses to score
any ask whose geometry guard fails and prints the band it landed on, mechanically.

Usage: python3 spikes/cartridge/c2_run.py [--url http://127.0.0.1:8005] [--temp 0.8]
           [--repeats 3] [--tag NAME] [--slack 8] [--dry-run]
  --dry-run renders every ask with the real template + a placeholder ack and prints expected
  sizes + tail tokens; contacts nothing (the rule that made v2_run safe to boot blind).
Writes state/c2_run_<tag>.jsonl (append) + state/c2_verdict_<tag>.txt.
"""
from __future__ import annotations
import argparse, datetime, json, re, sys, time, urllib.error, urllib.request as U
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "spikes" / "d2_dissect"))
from venue_render import load_tokenizer, load_template, live_render  # noqa: E402
from build_d2_plan_v2 import CUE_PREAMBLE  # noqa: E402

MARKERS = ("d2-archivist", "d2-metalsmith", "d2-recipe")
CUE_ECHO = {"d2-archivist": "voldranium", "d2-metalsmith": "klornish", "d2-recipe": "krevven"}
# the decoy bank: three unrelated invented facts, same shape, zero shared entities with the real
# bank; its golds are never counted as leaks (a decoy answer picking ITS OWN gold is interference-
# free behavior, a real gold there is the discriminator)
DECOY_FACTS = [
    {"name": "decoy-cartographer", "fact": "The chief cartographer of the Plenmarch Atoll is Doravel Thynn.",
     "cue": "Plenmarch-Atoll cartographer", "expect": ["doravel", "thynn"]},
    {"name": "decoy-port", "fact": "The main port of the Oskerry Compact is Vamlport-Cay.",
     "cue": "Oskerry-Compact main port", "expect": ["vamlport"]},
    {"name": "decoy-dish", "fact": "The traditional Nethri dish is braised cloudkelp with saltbread.",
     "cue": "the dish Nethris traditionally eat", "expect": ["cloudkelp"]},
]
LOG = ROOT / "strata-dev.log"


def http(url, payload=None, headers=None, timeout=960):
    """-> (status, parsed_body). Transport errors (socket.timeout included) come back as
    (0, {"raw": ...}): the worst-case in-request engine reload is ENGINE_READY_S=900
    (server.py:265) and idle_unload_s=700 makes it reachable; a failure is RECORDED, and the
    per-ask status checks below decide, never a lost-evidence traceback (audit 2026-10-11)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = U.Request(url, data=data, method="POST" if data is not None else "GET")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with U.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}
    except OSError as e:  # URLError/socket.timeout/connection-reset are OSError subclasses
        return 0, {"raw": f"transport error: {e}"}


def slot(url, action, filename):
    st, b = http(url + f"/slots/0?action={action}", {"filename": filename})
    if st != 200:
        raise SystemExit(f"{action} {filename!r} -> {st}: {json.dumps(b)[:200]} "
                         f"(venue blocked - run c1_plumb.py first)")
    return b


def log_mark():
    return LOG.stat().st_size if LOG.exists() else 0


def log_since(off):
    """the engine's per-request reuse bookkeeping printed between two byte offsets of
    strata-dev.log (binary seek: text-mode seek only trusts tell()-cookies, and the log mixes
    encodings). RESUME/REUSED go to engine STDOUT, which the server PIPES (generate.cpp:10512/
    11102 vs server.py:672-673) - they never reach this file; the same numbers arrive on stderr
    as the DONE summary "prompt N tokens = R reused + T read" (the line the server itself
    parses, server.py:355), so the regex matches both spellings. Dated amendment in
    C2_PRE_REG.md, 2026-10-11 audit."""
    if not LOG.exists():
        return []
    with LOG.open("rb") as f:
        f.seek(off)
        new = f.read().decode("utf-8", "replace")
    return [ln.strip() for ln in new.splitlines()
            if re.search(r"\b(RESUME|REUSED) \d+|= \d+ reused \+ \d+ read", ln)][:6]


def classify(ans, name, golds_by_pair):
    low = ans.lower()
    own = golds_by_pair.get(name) or []
    if any(g in low for g in own):
        return "gold"
    for other, gs in golds_by_pair.items():
        if other != name and any(g in low for g in gs):
            return "distractor"
    if any(w in low for w in CUE_ECHO.values() if w):  # PRE_REG:73-74 names all three cue words
        return "idk" if "idk" in low else "cue-echo"
    if "idk" in low:
        return "idk"
    return "confab"


def classify_decoy(ans):
    low = ans.lower()
    if any(g in low for d in DECOY_FACTS for g in d["expect"]):
        return "gold"
    return "idk" if "idk" in low else "confab"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--temp", type=float, default=0.8)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--tag", default=datetime.datetime.now().strftime("%H%M%S"))
    ap.add_argument("--slack", type=int, default=8)
    ap.add_argument("--maxtok", type=int, default=24)
    ap.add_argument("--acktok", type=int, default=16)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    url = a.url.rstrip("/")
    tag = re.sub(r"[^A-Za-z0-9_-]", "_", a.tag)
    if a.repeats < 3:
        raise SystemExit("--repeats >= 3 is mandatory protocol (C2_PRE_REG.md: temp-0.8 "
                         "single-shot verdicts are VOID by protocol)")

    tok = load_tokenizer()
    tpl = load_template()
    mk = json.loads((ROOT / "spikes/window/markers.json").read_text())
    cues = json.loads((ROOT / "spikes/d2_dissect/state/v2_build.json").read_text())["cue"]
    pair = {p["name"]: p for p in mk["pairs"] if p["name"] in MARKERS}
    golds_by_pair = {nm: [e.lower() for e in pair[nm]["expect"]] for nm in MARKERS}
    all_golds = sorted({g for gs in golds_by_pair.values() for g in gs})
    decoy_golds = sorted({g for d in DECOY_FACTS for g in d["expect"]})

    def question(nm):          # cue-last, exactly the v2 Venue A ask shape
        return f"{CUE_PREAMBLE} {cues[nm]}"

    # ---- dry-run: render everything, contact nothing ----------------------------------------
    if a.dry_run:
        out = []
        for nm in MARKERS:
            conv = []
            for i, other in enumerate(MARKERS):
                conv.append({"role": "user", "content": pair[other]["fact_text"]})
                ids, _ = live_render(tok, tpl, conv, "off")
                out.append({"arm": "encode", "name": nm, "turn": i + 1, "expect_ptok": len(ids)})
                conv.append({"role": "assistant", "content": "OK."})   # placeholder ack
            ask = conv + [{"role": "user", "content": question(nm)}]
            ids, _ = live_render(tok, tpl, ask, "off")
            bank_ids, _ = live_render(tok, tpl, conv, "off")
            out.append({"arm": "c2a/c2b", "name": nm, "expect_ptok": len(ids),
                        "bank_render": len(bank_ids), "tail_tokens": [tok.decode([t]) for t in ids[-4:]]})
            cold = [{"role": "user", "content": question(nm)}]
            ids, _ = live_render(tok, tpl, cold, "off")
            out.append({"arm": "cold", "name": nm, "expect_ptok": len(ids)})
            plumb = [{"role": "user", "content": f"{pair[nm]['fact_text']} Given only that, answer.\n\n"
                                                 f"{question(nm)}"}]
            ids, _ = live_render(tok, tpl, plumb, "off")
            out.append({"arm": "plumbing", "name": nm, "expect_ptok": len(ids)})
            dconv = []
            for d in DECOY_FACTS:
                dconv.append({"role": "user", "content": d["fact"]})
                dconv.append({"role": "assistant", "content": "OK."})
            ids, _ = live_render(tok, tpl, dconv + [{"role": "user", "content": question(nm)}], "off")
            out.append({"arm": "decoy_real", "name": nm, "expect_ptok": len(ids)})
            for d in DECOY_FACTS:
                ids, _ = live_render(tok, tpl,
                                     dconv + [{"role": "user", "content": f"{CUE_PREAMBLE} {d['cue']}"}], "off")
                out.append({"arm": "decoy-own", "name": d["name"], "expect_ptok": len(ids)})
        print(json.dumps(out, indent=1))
        print(f"dry-run: {len(out)} renders, no contact; bank size ground truth = save n_saved at "
              f"run time (acks are SAMPLED at temp {a.temp}, so bank_render is an estimate)")
        return 0

    # ---- live run ---------------------------------------------------------------------------
    st, health = http(url + "/health")
    model = health.get("model") or "strata"
    print(f"health {health.get('status')} model={model} max_context={health.get('max_context')} "
          f"tag={tag} channel=off/temp{a.temp}/memory-muted repeats={a.repeats}")

    (HERE / "state").mkdir(parents=True, exist_ok=True)
    out_jsonl = HERE / f"state/c2_run_{tag}.jsonl"
    recs = []

    def ask(arm, name, msgs, want, n_base=None, log_evidence=False, decoy_gold=False):
        ids, _ = live_render(tok, tpl, msgs, "off")
        body = {"model": model, "messages": msgs, "temperature": a.temp, "reasoning_effort": "off",
                "max_tokens": a.maxtok, "stream": False}
        off = log_mark() if log_evidence else 0
        t0 = time.time()
        st, r = http(url + "/v1/chat/completions", body, headers={"X-Strata-Memory": "off"})
        if st != 200:
            raise SystemExit(f"{arm} {name} -> {st}: {json.dumps(r)[:200]}")
        ch = (r.get("choices") or [{}])[0].get("message") or {}
        ans = (ch.get("content") or "").strip()
        ptok = (r.get("usage") or {}).get("prompt_tokens")
        t = r.get("timings") or {}
        cache_n, prompt_n = t.get("cache_n"), t.get("prompt_n")
        geom_ok = ptok == len(ids)
        ride_ok = None
        if n_base is not None:
            ride_ok = (cache_n is not None and cache_n >= n_base - a.slack
                       and prompt_n is not None and prompt_n <= (len(ids) - n_base) + a.slack)
        golds = all_golds
        leak = arm in ("cold", "decoy_real") and any(g in ans.lower() for g in golds)
        cls = classify_decoy(ans) if decoy_gold else classify(ans, name, golds_by_pair)
        rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "tag": tag, "pass": cur_pass, "arm": arm, "name": name, "answer": ans,
               "cls": cls, "want": want, "ptok": ptok, "expect_ptok": len(ids),
               "geom_ok": geom_ok, "cache_n": cache_n, "prompt_n": prompt_n,
               "n_base": n_base, "ride_ok": ride_ok, "leak": leak,
               "secs": round(time.time() - t0, 2)}
        if log_evidence:
            rec["engine_log"] = log_since(off)
        recs.append(rec)
        with out_jsonl.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        g = "geom-OK" if geom_ok else f"GEOM {ptok}/{len(ids)}"
        rd = "" if ride_ok is None else (" ride-OK" if ride_ok else " RIDE-NO-GO")
        print(f"[p{cur_pass}/{arm}] {name}: {g}{rd} cache_n={cache_n} prompt_n={prompt_n} "
              f"-> {ans[:70]!r} [{cls}]")
        return rec

    # decoy bank: encoded once per run, restored before each decoy control ask
    cur_pass = 0
    dconv = []
    for d in DECOY_FACTS:
        dconv.append({"role": "user", "content": d["fact"]})
        st, r = http(url + "/v1/chat/completions",
                     {"model": model, "messages": dconv, "temperature": a.temp,
                      "reasoning_effort": "off", "max_tokens": a.acktok, "stream": False},
                     headers={"X-Strata-Memory": "off"})
        if st != 200:  # audit: an unchecked 5xx here would append an EMPTY ack and silently
            raise SystemExit(f"decoy encode {d['name']} -> {st}: {json.dumps(r)[:160]} "
                             "(the bank's fact->ack adjacency would be silently broken)")
        dconv.append({"role": "assistant",
                      "content": (((r.get("choices") or [{}])[0].get("message") or {}).get("content")
                                  or "").strip()})  # the server emits content None for empties
    sv = slot(url, "save", f"c2_decoy_{tag}")
    decoy_base = sv["n_saved"]
    print(f"decoy bank: n_saved={decoy_base} save_ms={sv['timings']['save_ms']}")

    for cur_pass in range(1, a.repeats + 1):
        for nm in MARKERS:
            # encode the real bank: the WHOLE 3-fact panel as three user messages every time, so
            # every marker ask reads the same 3-fact bank (mini-C3 interference by design); the
            # first turn session-zeros the engine automatically (no prefix match)
            conv = []
            for other in MARKERS:
                conv.append({"role": "user", "content": pair[other]["fact_text"]})
                st, r = http(url + "/v1/chat/completions",
                             {"model": model, "messages": conv, "temperature": a.temp,
                              "reasoning_effort": "off", "max_tokens": a.acktok, "stream": False},
                             headers={"X-Strata-Memory": "off"})
                if st != 200:  # audit: a 5xx mid-encode would build a degenerate bank with
                    raise SystemExit(f"encode {other} -> {st}: {json.dumps(r)[:160]} "
                                     "(empty ack; the geometry guard cannot see this)")
                conv.append({"role": "assistant",
                             "content": (((r.get("choices") or [{}])[0].get("message") or {})
                                         .get("content") or "").strip()})
            fname = f"c2_bank_{tag}_p{cur_pass}_{nm.split('-', 1)[1]}"
            sv = slot(url, "save", fname)
            n_saved = sv["n_saved"]
            print(f"[p{cur_pass}/{nm}] bank encoded + saved: n_saved={n_saved} "
                  f"n_written={sv['n_written']} save_ms={sv['timings']['save_ms']}")
            # C2a: continue the live conversation (no restore in the loop)
            ask("c2a", nm, conv + [{"role": "user", "content": question(nm)}],
                golds_by_pair[nm], n_base=n_saved)
            # C2b: restore, identical list + question (the product shape)
            rs = slot(url, "restore", fname)
            ask("c2b", nm, conv + [{"role": "user", "content": question(nm)}],
                golds_by_pair[nm], n_base=rs["n_restored"], log_evidence=True)
            # controls
            ask("cold", nm, [{"role": "user", "content": question(nm)}], golds_by_pair[nm])
            ask("plumbing", nm, [{"role": "user",
                                  "content": f"{pair[nm]['fact_text']} Given only that, answer.\n\n"
                                             f"{question(nm)}"}], golds_by_pair[nm])
            rs = slot(url, "restore", f"c2_decoy_{tag}")
            ask("decoy_real", nm, dconv + [{"role": "user", "content": question(nm)}],
                golds_by_pair[nm], n_base=rs["n_restored"])  # PRE_REG:47 decoy base = the
            rs = slot(url, "restore", f"c2_decoy_{tag}")     # restore's own n_restored
            d = DECOY_FACTS[MARKERS.index(nm)]
            ask("decoy_own", d["name"], dconv + [{"role": "user", "content": f"{CUE_PREAMBLE} {d['cue']}"}],
                d["expect"], n_base=rs["n_restored"], decoy_gold=True)

    # ---- verdict, mechanically per C2_PRE_REG.md --------------------------------------------
    def scored(arm):
        return [r for r in recs if r["arm"] == arm and r["geom_ok"]]

    def gold_passes(arm):
        by = {}
        for r in scored(arm):
            by[r["pass"]] = by.get(r["pass"], 0) + (1 if r["cls"] == "gold" else 0)
        return by

    plumbing = scored("plumbing")
    plumb_gold = sum(1 for r in plumbing if r["cls"] == "gold")
    controls = scored("cold") + scored("decoy_real")
    leaks = [r for r in controls if r["leak"]]
    by_a, by_b = gold_passes("c2a"), gold_passes("c2b")
    rides_a, rides_b = scored("c2a"), scored("c2b")
    ride_a_ok = bool(rides_a) and all(r["ride_ok"] for r in rides_a)
    ride_b_ok = bool(rides_b) and all(r["ride_ok"] for r in rides_b)

    def band_ok(by):
        # PRE_REG:59 "gold >= 2/3 markers in >= 2 of 3 passes": TWO qualifying passes. The
        # original any() accepted ONE lucky pass - looser than registered (audit 2026-10-11).
        return sum(1 for v in by.values() if v >= 2) >= 2
    # guard 2 covers C2a/C2b/decoy asks, and an arm's PASS requires "guards green" (PRE_REG:59):
    # C2a's own ride result is now part of arm_a (finding 7 - a silently-failed C2a ride turns
    # the ask into plumbing-by-reread, not activation recall).
    guards_green_all = all(r["ride_ok"] for r in rides_a + rides_b + scored("decoy_real")
                           + scored("decoy_own"))
    arm_a = ride_a_ok and band_ok(by_a)
    arm_b = ride_b_ok and band_ok(by_b)
    geom_all = all(r["geom_ok"] for r in recs)

    if len(plumbing) < 9 or plumb_gold < len(plumbing):
        verdict = (f"VOID - plumbing gate: {plumb_gold}/{len(plumbing)} in-prompt positives "
                   "answered; this boot supports no C2 verdict")
    elif leaks:
        verdict = ("VOID - control leak: " +
                   str([{"arm": r["arm"], "name": r["name"], "answer": r["answer"][:60]} for r in leaks]))
    elif not geom_all:
        n_bad = sum(1 for r in recs if not r["geom_ok"])
        verdict = f"VOID - geometry guard failed on {n_bad} ask(s); renders do not match the engine"
    elif arm_a and not ride_b_ok:
        bad = [r for r in rides_b if not r["ride_ok"]]
        sample = f" (cache_n {bad[0]['cache_n']} vs base {bad[0]['n_base']} typical)" if bad else ""
        verdict = (f"INSTALL-BROKEN - C2a passes (per-pass {by_a}, rides green) but C2b ride "
                   f"guard failed on {len(bad)}/{len(rides_b)} asks{sample}: the memory works, "
                   "the install path is the suspect - C1-level ride debugging first, not a "
                   "memory verdict")
    elif arm_a or arm_b:
        verdict = (f"PASS - CARTRIDGE RECALL WORKS on Strata (PRE_REG:60: EITHER arm alone is a "
                   f"PASS): C2a per-pass {by_a} (rides green={ride_a_ok}), C2b per-pass {by_b} "
                   f"(rides green={ride_b_ok}); proceed to C3 (100-fact head-to-head)")
    elif all(v == 0 for v in list(by_a.values()) + list(by_b.values())) and guards_green_all:
        verdict = (f"READOUT MISS - gold 0/{len(rides_a)} + 0/{len(rides_b)} in BOTH arms over "
                   f"{a.repeats} passes at temp {a.temp}, plumbing {plumb_gold}/{len(plumbing)}, "
                   "ride guards green: whole-state activation replay does not surface the gold "
                   "-> tier-2 needs MAM-style cut-layer surgery or the grafting tier; the lease "
                   "is up for renegotiation")
    else:
        verdict = (f"MECHANISM-SENSITIVE - C2a per-pass {by_a} (rides green={ride_a_ok}), C2b "
                   f"per-pass {by_b} (rides green={ride_b_ok}), all guarded rides green="
                   f"{guards_green_all}; between bands - recorded, stop, no venue-hunting "
                   "without a new pre-registration")

    dist = {}
    for r in scored("c2a") + scored("c2b"):
        dist[r["cls"]] = dist.get(r["cls"], 0) + 1
    decoy_own = scored("decoy_own")
    tail = (f"\nC2 verdict @{datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')} "
            f"tag={tag} channel=off/temp{a.temp}/memory-muted repeats={a.repeats}\n"
            f"  plumbing {plumb_gold}/{len(plumbing)} | controls {len(controls)} asks, leaks "
            f"{len(leaks)} | C2a gold per-pass {by_a} (rides green={ride_a_ok}) | C2b gold "
            f"per-pass {by_b} (rides green={ride_b_ok}) | all guarded rides green="
            f"{guards_green_all} | C2b engine-log reuse-summary captured on "
            f"{sum(1 for r in recs if r.get('engine_log'))}/{len(scored('c2b'))} asks | "
            f"decoy-own bonus {sum(1 for r in decoy_own if r['cls'] == 'gold')}/{len(decoy_own)} | "
            f"answer classes (both marker arms) {dist}\n"
            f"  {verdict}\n")
    print(tail)
    (HERE / f"state/c2_verdict_{tag}.txt").write_text(
        "\n".join(f"[p{r['pass']}/{r['arm']}] {r['name']} cls={r['cls']} "
                  f"ptok={r['ptok']}/{r['expect_ptok']} cache_n={r['cache_n']} "
                  f"prompt_n={r['prompt_n']} base={r['n_base']} ride={r['ride_ok']} :: {r['answer']}"
                  for r in recs) + tail)
    return 1 if verdict.startswith("VOID") else 0  # scripting can gate on the exit code too


if __name__ == "__main__":
    sys.exit(main())
