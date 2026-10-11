#!/usr/bin/env python3
"""spikes/cartridge/c3v3_run.py - the install-swap purity probe (bands + venue PRE-REGISTERED
in C3V3_PRE_REG.md - read that first; written before any v3 ask).

One question, one confound-free instrument: after a restore replaces live state with a
DIFFERENT bank, can the previous bank's activations leak into the new conversation? Real-world
golds cannot ask this (priors fire everywhere - the v1/v2 forensics); NONCE golds can: the 3 D2
marker facts (zero weights path, cold idk proven) are embedded in bank2, and the probe asks
their cues after the decoy bank is installed. Any nonce gold = PERSISTENCE-DETECTED; 0/9 =
SWAP-CLEAN-AT-SCALE. A 9/9 positive control (same cues riding bank2 directly) gates the whole
probe - it must be able to see what it is looking for.

Venue: bank2 = 100 Gate-1 facts + 3 marker facts (103 user messages + sampled acks, one save);
decoy2 = the 100 Smokeport nonce facts. Channel off/temp 0.8/muted/max_tokens 24, geometry
guard per ask (any VOID = run VOID), ride guard on every probe ask (cache_n == the restore's
n_restored - slack: the engine's own bookkeeping that the foreign bank is the whole prefix),
texture = the six v2-noisy real rows x 3 under decoy2 (recorded, never banded).

Usage: python3 spikes/cartridge/c3v3_run.py [--url http://127.0.0.1:8005] [--tag NAME]
           [--slack 8] [--dry-run]
Writes state/c3v3_run_<tag>.jsonl (append) + state/c3v3_verdict_<tag>.txt.
"""
from __future__ import annotations
import argparse, collections, datetime, json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "spikes" / "d2_dissect"))
sys.path.insert(0, str(ROOT / "spikes" / "window"))
from venue_render import load_tokenizer, load_template, live_render  # noqa: E402
from gate1_driver import rows, hit  # noqa: E402
from build_d2_plan_v2 import CUE_PREAMBLE  # noqa: E402
from c3_run import http, slot, log_mark, log_since, decoy_facts  # audit-hardened machinery  # noqa: E402

MARKERS = ("d2-archivist", "d2-metalsmith", "d2-recipe")
NOISY = (8, 9, 28, 55, 61, 86)  # 0-based rows 9,10,29,56,62,87 = the v2 decoy-noise set


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--temp", type=float, default=0.8)
    ap.add_argument("--tag", default=datetime.datetime.now().strftime("%H%M%S"))
    ap.add_argument("--slack", type=int, default=8)
    ap.add_argument("--maxtok", type=int, default=24)
    ap.add_argument("--acktok", type=int, default=16)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    url = a.url.rstrip("/")
    tag = a.tag

    tok = load_tokenizer()
    tpl = load_template()
    R = rows()
    mk = {p["name"]: p for p in json.loads((ROOT / "spikes/window/markers.json").read_text())["pairs"]}
    cues = json.loads((ROOT / "spikes/d2_dissect/state/v2_build.json").read_text())["cue"]
    own_golds = {nm: [e.lower() for e in mk[nm]["expect"]] for nm in MARKERS}
    any_nonce = [g for gs in own_golds.values() for g in gs]
    DECOY = decoy_facts()

    def fact(i):
        return f"{R[i]['question']} Answer: {R[i]['expected_answer_contains']}."

    def cue_msg(nm):
        return {"role": "user", "content": f"{CUE_PREAMBLE} {cues[nm]}"}

    if a.dry_run:
        bank2 = []
        for i in range(100):
            bank2 += [{"role": "user", "content": fact(i)}, {"role": "assistant", "content": "OK."}]
        for nm in MARKERS:
            bank2 += [{"role": "user", "content": mk[nm]["fact_text"]},
                      {"role": "assistant", "content": "OK."}]
        dconv = []
        for d in DECOY:
            dconv += [{"role": "user", "content": d["fact"]}, {"role": "assistant", "content": "OK."}]
        out = {}
        for nm in MARKERS:
            ids, _ = live_render(tok, tpl, bank2 + [cue_msg(nm)], "off")
            out[f"bank2+cue:{nm}"] = len(ids)
            ids, _ = live_render(tok, tpl, dconv + [cue_msg(nm)], "off")
            out[f"decoy2+cue:{nm}"] = len(ids)
        print(json.dumps(out, indent=1))
        print("dry-run: no contact; bank2 ground truth = n_saved at run time (acks sampled)")
        return 0

    st, health = http(url + "/health")
    if st != 200:
        raise SystemExit(f"/health -> {st}: {json.dumps(health)[:160]} - no run")
    model = health.get("model") or "strata"
    print(f"health {health.get('status')} model={model} tag={tag} "
          f"channel=off/temp{a.temp}/memory-muted probe=c3v3-swap-purity")

    (HERE / "state").mkdir(parents=True, exist_ok=True)
    out_jsonl = HERE / f"state/c3v3_run_{tag}.jsonl"
    recs = []

    def ask(arm, name, msgs, p, golds, n_base=None, log_evidence=False, scorer=None):
        ids, _ = live_render(tok, tpl, msgs, "off")
        body = {"model": model, "messages": msgs, "temperature": a.temp,
                "reasoning_effort": "off", "max_tokens": a.maxtok, "stream": False}
        off = log_mark() if log_evidence else 0
        t0 = time.time()
        st, r = http(url + "/v1/chat/completions", body, headers={"X-Strata-Memory": "off"})
        ans = (f"HTTP {st}: {json.dumps(r)[:110]}" if st != 200 else
               (((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip())
        ptok = (r.get("usage") or {}).get("prompt_tokens")
        t = r.get("timings") or {}
        cache_n, prompt_n = t.get("cache_n"), t.get("prompt_n")
        geom_ok = ptok == len(ids)
        ride_ok = (cache_n is not None and cache_n >= n_base - a.slack
                   and prompt_n is not None and prompt_n <= (len(ids) - n_base) + a.slack)
        low = ans.lower()
        gold = (bool(scorer(ans, golds[0])) if scorer else any(g in low for g in golds))
        rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "tag": tag, "pass": p, "arm": arm, "name": name, "answer": ans, "gold": gold,
               "ptok": ptok, "expect_ptok": len(ids), "geom_ok": geom_ok, "cache_n": cache_n,
               "prompt_n": prompt_n, "n_base": n_base, "ride_ok": ride_ok,
               "secs": round(time.time() - t0, 2)}
        if log_evidence:
            rec["engine_log"] = log_since(off)
        recs.append(rec)
        with out_jsonl.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"[p{p}/{arm}] {name}: geom {'OK' if geom_ok else f'{ptok}/{len(ids)}'} "
              f"ride {'OK' if ride_ok else 'NO-GO'} cache_n={cache_n} prompt_n={prompt_n} "
              f"-> {ans[:60]!r} {'GOLD' if gold else '-'}")
        return rec

    def encode(facts, what):
        conv = []
        for i, f in enumerate(facts):
            conv.append({"role": "user", "content": f})
            st, r = http(url + "/v1/chat/completions",
                         {"model": model, "messages": conv, "temperature": a.temp,
                          "reasoning_effort": "off", "max_tokens": a.acktok, "stream": False},
                         headers={"X-Strata-Memory": "off"})
            if st != 200:
                raise SystemExit(f"{what} encode turn {i + 1} -> {st}: {json.dumps(r)[:160]}")
            conv.append({"role": "assistant",
                         "content": (((r.get("choices") or [{}])[0].get("message") or {})
                                     .get("content") or "").strip()})
        return conv

    # bank2: the whole real bank + the 3 marker nonce facts, one save
    t0 = time.time()
    bank2 = encode([fact(i) for i in range(100)] + [mk[nm]["fact_text"] for nm in MARKERS], "bank2")
    sv = slot(url, "save", f"c3v3_bank_{tag}")
    print(f"bank2: 103 facts encoded in {time.time() - t0:.0f} s, n_saved={sv['n_saved']} "
          f"n_written={sv['n_written']} B save_ms={sv['timings']['save_ms']}")

    pos = []
    for p in (1, 2, 3):  # positive control: the probe must SEE the nonces when present
        for nm in MARKERS:
            rs = slot(url, "restore", f"c3v3_bank_{tag}")
            pos.append(ask("positive", nm, bank2 + [cue_msg(nm)], p, own_golds[nm],
                           n_base=rs["n_restored"], log_evidence=(p == 1)))
    pos_gold = sum(1 for r in pos if r["gold"])

    t0 = time.time()
    dconv = encode([d["fact"] for d in DECOY], "decoy2")
    sv = slot(url, "save", f"c3v3_decoy_{tag}")
    print(f"decoy2: encoded in {time.time() - t0:.0f} s, n_saved={sv['n_saved']}")

    pers = []
    for p in (1, 2, 3):  # the swap test: ANY nonce gold under the foreign install = persistence
        for nm in MARKERS:
            rs = slot(url, "restore", f"c3v3_decoy_{tag}")
            pers.append(ask("persist", nm, dconv + [cue_msg(nm)], p, any_nonce,
                            n_base=rs["n_restored"], log_evidence=(p == 1)))
    hits = [r for r in pers if r["gold"]]

    tex = collections.Counter()
    for p in (1, 2, 3):  # prior-strength texture: recorded, never banded
        for i in NOISY:
            rs = slot(url, "restore", f"c3v3_decoy_{tag}")
            rec = ask("texture", f"row{i + 1}",
                      dconv + [{"role": "user", "content": R[i]["question"]}], p,
                      [R[i]["expected_answer_contains"]], n_base=rs["n_restored"], scorer=hit)
            if rec["gold"]:
                tex[f"row{i + 1}"] += 1

    # ---- verdict, mechanically per C3V3_PRE_REG.md ------------------------------------------
    geom_void = [r for r in recs if not r["geom_ok"]]
    rides = pos + pers + [r for r in recs if r["arm"] == "texture"]
    ride_all_ok = bool(pers) and all(r["ride_ok"] for r in rides)
    if pos_gold < 9:
        verdict = (f"VOID - positive control {pos_gold}/9: the probe cannot see its own target "
                   "when installed - it detects nothing; no persistence claim either way")
    elif geom_void:
        verdict = (f"VOID - geometry guard failed on {len(geom_void)} ask(s): "
                   f"{[(r['arm'], r['name']) for r in geom_void][:5]}")
    elif not ride_all_ok:
        bad = [r for r in rides if not r["ride_ok"]]
        verdict = (f"INSTALL-BROKEN - ride guard failed on {len(bad)}/{len(rides)} asks "
                   f"(cache_n {bad[0]['cache_n']} vs base {bad[0]['n_base']}): every probe ask "
                   "must provably ride the FOREIGN bank")
    elif hits:
        verdict = (f"PERSISTENCE-DETECTED - {len(hits)}/9 persistence asks surfaced a nonce "
                   f"gold under the decoy install: {[(r['name'], r['answer'][:40]) for r in hits]}"
                   " - stale state reaches fresh conversations; the install is not a clean "
                   "swap at scale, and the product shape needs a session-zero before install")
    else:
        verdict = ("SWAP-CLEAN-AT-SCALE - 0/9 nonce golds under the decoy install at 103+100 "
                   "facts, positive control 9/9, every probe ask rode the foreign bank exactly: "
                   "install is state SELECTION, the previous bank's activations do not leak")

    tail = (f"\nC3-v3 verdict @{datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')} "
            f"tag={tag} channel=off/temp{a.temp}/memory-muted\n"
            f"  positive {pos_gold}/9 | persistence nonce golds {len(hits)}/9 | geometry VOID "
            f"{len(geom_void)} | rides green={ride_all_ok} ({len(rides)} guarded asks) | texture "
            f"gold-counts {dict(sorted(tex.items(), key=lambda kv: int(kv[0][3:])))} (3/3 rows "
            f"are prior-strength notes, NOT persistence - the nonce channel owns that verdict) | "
            f"reuse-summary captured on {sum(1 for r in recs if r.get('engine_log') is not None)}/"
            f"{sum(1 for r in recs if 'engine_log' in r)} marked "
            f"asks | persist ask seconds avg "
            f"{round(sum(r['secs'] for r in pers) / len(pers), 2) if pers else 'n/a'}\n"
            f"  {verdict}\n")
    print(tail)
    (HERE / f"state/c3v3_verdict_{tag}.txt").write_text(
        "\n".join(f"[p{r['pass']}/{r['arm']}] {r['name']} gold={r['gold']} "
                  f"ptok={r['ptok']}/{r['expect_ptok']} cache_n={r['cache_n']} "
                  f"prompt_n={r['prompt_n']} base={r['n_base']} ride={r['ride_ok']} :: {r['answer']}"
                  for r in recs) + tail)
    return 1 if verdict.startswith(("VOID", "INSTALL")) else 0


if __name__ == "__main__":
    sys.exit(main())
