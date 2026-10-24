#!/usr/bin/env python3
"""spikes/cartridge/c4_run.py - the bank-capacity ladder (bands + venue PRE-REGISTERED in
C4_PRE_REG.md, SIGNED 2026-10-24 evening; read that first - the runner follows the pen).

One question: does standing recall hold as the bank grows 100 -> 500 -> 1,000 facts, and what
does each rung cost? C3 banked 100/100 at the 100-fact bank; the ceiling ABOVE that is the
open driver, and this probe's product is the CURVE, not a verdict.

Shape (the C-series machinery, imported from c3_run so the guards are the same instruments
that banked C2/C3/C3-v3):

  rung 100   = CONTROL, C3's exact instrument: all 100 Gate-1 real facts ("{question} Answer:
               {gold}.") encoded, saved once, re-measured THIS boot's era for comparability,
               scored by gate1_driver.hit (the strict banked scorer). Any other rung number
               runs the NONCE family instead (so --rungs 25 is a plumbing smoke).
  nonce bank = decoy_facts' generator (c3_run's D_KINDS/D_STEMS, Smokeport towns) extended to
               arbitrary index ranges: bank = indices 1000.., decoys = indices 2000.. (towns
               and golds embed the index, so every marker is unique - exact-marker scoring,
               no judge, per contract). The control's decoy stays C3's own Smokeport 100
               (decoy_facts()) so rung 100's leak rate is comparable with C3's decoy_real arm.
  NESTED     = bank500 is the message-prefix of bank1000 (C5's amendment law); acks replayed
               verbatim (c3v3's byte-exactness law), so a bigger rung EXTENDS the encode
               instead of re-paying it. Decoys nest the same way.
  cold gate  = every nonce fact idk-proven BLIND (bare ask, fresh muted conversation) BEFORE
               install; a prior hit is REPLACED by the next pool index and re-probed until the
               family resolves all-cold (Gate-1 law, non-negotiable - priors voided C3-v2).
               Verdicts cache in the bench dir, so re-runs do not re-pay the probe. Gate-1's
               100 ride C3's own cold arm at the control rung (any gold = contamination).
  recall arm = --repeats (contract: 3; <3 refused outside --smoke) x one ask per fact: restore
               -> identical bank message list + the bare ask as final turn (the product
               shape). Geometry guard per ask: ptok == the local render PLUS whatever drift
               THIS boot shows between usage and render, measured at run start by one
               render-diff probe ask in the exact shape the probes use - the {REASON:off}
               lesson of 2026-10-24 (see the scar note at the probe: the sweep's cold gate
               went blind for a week trusting a drifted constant, and this runner's first
               smoke voided 38/38 asks copying the sweep's probe whole - the guard works).
  leak arm   = the c3v3 template at scale: the decoy bank installed, the BANK's asks re-asked
               under it (1 pass; --leak-sample thins); a gold = the previous bank persisting
               through the swap. Gate: rung N's leak rate must not exceed rung 100's observed
               rate; binomial allowance (normal approximation, one-sided) computed and STATED
               at run time per contract.
  plumbing   = c3's read-path sanity gate (fact + ask in ONE message, no bank): all 100 at the
               control rung, sampled elsewhere. Recorded; a collapse (<90%) marks the rung VOID.
  cost ledger= per-ask latency (mean/p95 s) + prompt tokens; bank prefix tokens (n_saved);
               encode wall-time and save_ms as install; the first encode turn after an
               eviction records its prompt_n as the RE-SEED price (the resident-prefix cost
               the contract insists is measured, not assumed). Reported, never gated. One
               displacement line per rung: a memory-ON ask - on THIS box the standing text is
               the prod cartridge, so the line is a budget-headroom note; the bank-as-standing
               variant is the CONFIG venue the contract names, and the run record must say
               which box carried it.

Robustness (c3's audit law): transport/5xx recorded per ask, never a crash; one retry, then
the ask is FAILED (excluded from recall's numerator, kept in the denominator - a dead server
must not inflate a rung); timeouts 960 s; the cold gate FAILS LOUD (SystemExit) on transport
death, because a fact cached "cold" without a real probe is a lie. Geometry VOID (ptok !=
render + delta with a real response) voids the run. Slot .bin files ride the engine's
slot_save_path like C5's did and are deleted after each rung's verdicts (--keep-files;
slot_retain is 6 and the prod cartridge lives there too). Run records land in
Strata-data/bench/c4/ - the durability law of 2026-10-24: /tmp is not a vault.

Usage: python3 spikes/cartridge/c4_run.py [--url http://127.0.0.1:8005] [--temp 0.8]
           [--rungs 100,500,1000] [--repeats 3] [--leak-sample 0] [--smoke]
           [--slack 8] [--maxtok 24] [--acktok 16] [--tag NAME] [--keep-files] [--dry-run]
Writes bench/c4/c4_run_<tag>.jsonl (append) + bench/c4/c4_verdict_<tag>.txt.

A scar for the RESULTS addendum (written before any C4 ask, so it is not an excuse later):
the contract's displacement clause says the standing text "currently caps at 12,000 chars";
the code's real number is cartridge_text(max_chars=4000) (serve/memory.py, M3b), config-
overridable, and SOUL_BUDGET is 16,000. The 12,000 was my morning self reaching for a number
- annotate, never erase: the run record must restate the cap from the code, not from this file.
"""
from __future__ import annotations
import argparse, datetime, json, math, re, statistics, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BENCH = Path("/home/eugene/Strata-data/bench/c4")
SESSIONS = Path("/home/eugene/Strata-data/sessions")      # this box's slot_save_path (config)
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "spikes" / "d2_dissect"))
sys.path.insert(0, str(ROOT / "spikes" / "window"))
from venue_render import load_tokenizer, load_template, live_render  # noqa: E402
from gate1_driver import rows, hit  # noqa: E402
from c3_run import http, slot, decoy_facts, D_KINDS, D_STEMS  # noqa: E402

IDK = re.compile(r"\bidk\b|do(n'?t| n'?t) know|no (mention|idea)|not (in|mentioned|stated)", re.I)
BANK0, DEC0 = 1000, 2000  # disjoint index ranges: decoy towns can never collide with bank towns


def nonce(i):
    """decoy_facts' formula, extended to any index: town and gold embed i, markers stay unique."""
    kind, stem = D_KINDS[i % 4], D_STEMS[i % 8]
    town, gold = f"{stem}{i}", f"Zivvleax{i}-q{chr(97 + i % 26)}"
    return {"i": i, "kind": kind, "town": town, "gold": gold,
            "fact": f"The {kind} of {town} is {gold}.",
            "ask": f"According to the bank, what is the {kind} of {town}? Answer with just the name."}


def classify(ans, gold, others):
    low = ans.lower()
    if gold.lower() in low:
        return "gold"
    if any(g.lower() in low for g in others):
        return "distractor"                      # answered, wrong drawer: in-bank interference
    return "idk" if IDK.search(ans) else "other"


def binom_ge(k, n, p0):
    """One-sided normal approximation (no continuity lie in it: -0.5 correction stated)."""
    if n == 0:
        return 1.0
    mu, sd = n * p0, max((n * p0 * (1 - p0)) ** 0.5, 1e-9)
    z = (k - 0.5 - mu) / sd
    return 0.5 * math.erfc(z / math.sqrt(2)) if z > 0 else 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--temp", type=float, default=0.8)
    ap.add_argument("--rungs", default="100,500,1000")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--leak-sample", type=int, default=0)          # 0 = every bank ask re-asked
    ap.add_argument("--smoke", action="store_true")                # allows repeats<3; marks verdict
    ap.add_argument("--slack", type=int, default=8)
    ap.add_argument("--maxtok", type=int, default=24)
    ap.add_argument("--acktok", type=int, default=16)
    ap.add_argument("--tag", default=datetime.datetime.now().strftime("%H%M%S"))
    ap.add_argument("--keep-files", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    url = a.url.rstrip("/")
    rungs = sorted(int(x) for x in a.rungs.split(","))
    if not a.smoke and a.repeats < 3 and not a.dry_run:
        raise SystemExit("--repeats < 3 is refused (>=3 repeats law) - or pass --smoke")
    if a.temp == 0:
        raise SystemExit("temp 0 refused on this build (user-measurement law)")
    control = 100 in rungs                                          # rung 100 = C3's exact instrument

    R = rows()
    gate1 = [{"i": i, "kind": "gate1", "town": None, "gold": R[i]["expected_answer_contains"],
              "fact": f"{R[i]['question']} Answer: {R[i]['expected_answer_contains']}.",
              "ask": R[i]["question"]} for i in range(100)]
    BENCH.mkdir(parents=True, exist_ok=True)
    cold_cache = BENCH / "c4_cold_known.jsonl"
    known = {}
    if cold_cache.exists():
        for ln in cold_cache.read_text().splitlines():
            r = json.loads(ln)
            known[r["i"]] = r["verdict"]                            # i -> "cold" | "prior"

    need_probe, seen = [], set()

    def family(kind, start, n):
        """First n COLD facts from index start up (unprobed ones join need_probe once each).
        Gate-1 is cold BY SELECTION - its own cold arm re-checks at the control rung, c3's way."""
        out, i = [], start
        while len(out) < n:
            f = gate1[i] if kind == "gate1" else nonce(i)
            i += 1
            if known.get(f["i"]) == "prior":
                continue                                            # replaced forever
            out.append(f)
            if kind != "gate1" and f["i"] not in known and f["i"] not in seen:
                seen.add(f["i"])
                need_probe.append(f)
        return out

    def resolve():
        bank = {N: family("gate1" if N == 100 else "nonce", 0 if N == 100 else BANK0, N)
                for N in rungs}
        dec_n = max((N for N in rungs if N != 100), default=0)
        return bank, (family("nonce", DEC0, dec_n) if dec_n else [])

    bank, dec = resolve()

    if a.dry_run:
        tok, tpl = load_tokenizer(), load_template()
        plan = {}
        for N in rungs:
            conv = [m for f in bank[N] for m in
                    ({"role": "user", "content": f["fact"]},
                     {"role": "assistant", "content": "OK."})]
            ids, _ = live_render(tok, tpl, conv + [{"role": "user", "content": bank[N][-1]["ask"]}],
                                 "off")
            plan[f"bank{N}"] = {"prefix_msgs": len(conv), "prefix_plus_ask_tok": len(ids),
                                "est_asks": {"encode": N, "probe": N * a.repeats,
                                             "leak": a.leak_sample or N,
                                             "plumb": min(N, 100) if N == 100 else (a.leak_sample or 50)}}
        print(json.dumps({"tag": a.tag, "temp": a.temp, "repeats": a.repeats, "rungs": rungs,
                          "cold_to_probe_first": len(need_probe), "plan": plan}, indent=1))
        print("dry-run: no contact; bank ground truth = save n_saved at run time (acks sampled)")
        return 0

    st, health = http(url + "/health")
    if st != 200:
        raise SystemExit(f"/health -> {st}: {json.dumps(health)[:160]} - no run")
    model = health.get("model") or "strata"
    tok, tpl = load_tokenizer(), load_template()
    out_jsonl = BENCH / f"c4_run_{a.tag}.jsonl"
    recs = []

    def now():
        return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    def rec(r):
        recs.append(r)
        with out_jsonl.open("a") as fh:
            fh.write(json.dumps(r) + "\n")

    ddelta = 0

    def ask(arm, rung, name, msgs, p, gold, others, n_base=None):
        ids, _ = live_render(tok, tpl, msgs, "off")
        body = {"model": model, "messages": msgs, "temperature": a.temp,
                "reasoning_effort": "off", "max_tokens": a.maxtok, "stream": False}
        t0 = time.time()
        st, r = http(url + "/v1/chat/completions", body, headers={"X-Strata-Memory": "off"})
        ans = (f"HTTP {st}: {json.dumps(r)[:110]}" if st != 200 else
               (((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip())
        ptok = (r.get("usage") or {}).get("prompt_tokens")
        t = r.get("timings") or {}
        cache_n, prompt_n = t.get("cache_n"), t.get("prompt_n")
        geom_ok = ptok is not None and ptok == len(ids) + ddelta
        ride_ok = (n_base is None) or (cache_n is not None and cache_n >= n_base - a.slack
                                       and prompt_n is not None
                                       and prompt_n <= (len(ids) + ddelta - n_base) + a.slack)
        cls = classify(ans, gold, others)
        rec({"ts": now(), "tag": a.tag, "arm": arm, "rung": rung, "pass": p, "name": name,
             "answer": ans[:200], "cls": cls, "ptok": ptok, "want_ptok": len(ids) + ddelta,
             "geom_ok": geom_ok, "cache_n": cache_n, "prompt_n": prompt_n, "n_base": n_base,
             "ride_ok": ride_ok, "secs": round(time.time() - t0, 2)})
        return cls

    def ask_retry(arm, rung, name, msgs, p, gold, others, n_base=None):
        """One retry on transport death (c3's per-ask robustness law); a second death stays
        recorded as a FAILED ask (ptok None) - the caller counts it as a miss, never a pass."""
        cls = ask(arm, rung, name, msgs, p, gold, others, n_base)
        if recs[-1]["ptok"] is None:
            cls2 = ask(arm, rung, name + ":retry", msgs, p, gold, others, n_base)
            recs[-2]["retried"] = True
            return cls2, (recs[-1]["ptok"] is None)
        return cls, False
    # ask_retry returns (cls, failed)

    def encode(facts, conv, what, rung):
        for j, f in enumerate(facts):
            conv.append({"role": "user", "content": f["fact"]})
            t0 = time.time()
            st, r = http(url + "/v1/chat/completions",
                         {"model": model, "messages": list(conv), "temperature": a.temp,
                          "reasoning_effort": "off", "max_tokens": a.acktok, "stream": False},
                         headers={"X-Strata-Memory": "off"})
            if st != 200:
                raise SystemExit(f"{what} encode turn {j + 1} -> {st}: {json.dumps(r)[:160]}")
            ack = (((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            conv.append({"role": "assistant", "content": ack})
            if j == 0:      # the first turn after any eviction = the re-seed price (contract)
                rec({"ts": now(), "tag": a.tag, "arm": "reseed", "rung": rung,
                     "name": f"{what}-first-turn",
                     "prompt_n": (r.get("timings") or {}).get("prompt_n"),
                     "secs": round(time.time() - t0, 2)})
        return conv

    # ---- geometry: measure THIS boot's usage-vs-render drift for THIS runner's send shape --
    # The sweep's cold gate went blind for a week because the {REASON:off} TAG IT SENDS IN
    # CONTENT drifted in cost with the boot; its two-content probe measures that tag, and its
    # want = render + tag is coherent with its own asks. THIS runner sends no in-content tag
    # (plain msgs + reasoning_effort:"off"), so the honest measurement is a render-diff on the
    # exact shape the probes use: one ask, usage minus its own local render. The first smoke
    # (2026-10-24 night) caught me copying the sweep's probe whole: +7 phantom on all 38
    # guarded asks - annotate, never erase: the guard earned its keep on contact.
    def probe_ask(content):
        body = {"model": model, "messages": [{"role": "user", "content": content}],
                "temperature": a.temp, "reasoning_effort": "off", "max_tokens": 1, "stream": False}
        st, r = http(url + "/v1/chat/completions", body, headers={"X-Strata-Memory": "off"},
                     timeout=60)
        return (r.get("usage") or {}).get("prompt_tokens") if st == 200 else None
    ids0, _ = live_render(tok, tpl, [{"role": "user", "content": "hi"}], "off")
    u0 = probe_ask("hi")
    if u0 is None:
        raise SystemExit("[geom] probe ask hit transport death on a live /health - refusing "
                         "to bank a 0 sight unseen")
    ddelta = u0 - len(ids0)
    print(f"health model={model} tag={a.tag} rungs={rungs} repeats={a.repeats} "
          f"channel=off/temp{a.temp}/memory-muted | [geom] usage-vs-render drift this boot: "
          f"{ddelta:+d} tok", flush=True)

    # ---- cold gate: loop until the family resolves all-cold (replacements included) --------
    reps = 0
    while True:
        todo = [f for f in need_probe if f["i"] not in known]
        if not todo:
            break
        for f in todo:
            cls, failed = ask_retry("cold", 0, f"nonce{f['i']}",
                                    [{"role": "user", "content": f["ask"]}], 0, f["gold"], [])
            if failed:
                raise SystemExit(f"[cold] transport death probing nonce{f['i']} - no run "
                                 "(a fact cached cold without a real probe is a lie)")
            verdict = "prior" if cls == "gold" else "cold"
            known[f["i"]] = verdict
            with cold_cache.open("a") as fh:
                fh.write(json.dumps({"i": f["i"], "verdict": verdict, "gold": f["gold"],
                                     "tag": a.tag}) + "\n")
            if verdict == "prior":
                reps += 1
                print(f"[cold] nonce{f['i']} PRIOR ({f['gold']}) - replaced", flush=True)
        bank, dec = resolve()                                    # re-walk with fresh knowings
    if reps:
        print(f"[cold] family resolved after {reps} replacement(s)", flush=True)

    geom_void, failed_total, ledger = [], 0, {}
    nonce_conv = {}                                              # nested banks: rung -> message list

    for N in rungs:
        fam = bank[N]
        golds_all = [f["gold"] for f in fam]
        cold_contam = None
        if N == 100 and control:                                 # c3's cold arm, verbatim intent
            cold_contam = sum(ask_retry("cold100", 100, f"row{f['i'] + 1}",
                                        [{"role": "user", "content": f["ask"]}], 0,
                                        f["gold"], [])[0] == "gold" for f in fam)
            print(f"[cold100] control-rung cold check: {cold_contam} prior hits (expect 0)",
                  flush=True)
        # 1) encode + save (nested: a nonce rung extends the previous nonce rung's conv)
        t0 = time.time()
        if N == 100:
            conv_b = encode(fam, [], "bank100", 100)
        else:
            prev = max((x for x in rungs if x < N and x != 100), default=None)
            base = list(nonce_conv[prev]) if prev else []
            conv_b = encode(fam[len(base) // 2:], base, f"bank{N}", N)
            nonce_conv[N] = conv_b
        encode_s = round(time.time() - t0, 1)
        sv = slot(url, "save", f"c4_bank{N}_{a.tag}")
        print(f"bank{N}: {len(fam)} facts encoded in {encode_s} s, n_saved={sv['n_saved']} tok "
              f"n_written={sv['n_written'] / 1e6:.0f} MB save_ms={sv['timings']['save_ms']}",
              flush=True)
        # 2) recall arm: repeats x restore x bare ask; strict scorer at the control rung
        per_pass, first_restore = [], None
        for p in range(1, a.repeats + 1):
            n_gold = 0
            for f in fam:
                rs = slot(url, "restore", f"c4_bank{N}_{a.tag}")
                if first_restore is None:
                    first_restore = rs
                cls, failed = ask_retry("recall", N,
                                        f"row{f['i'] + 1}" if N == 100 else f"nonce{f['i']}",
                                        conv_b + [{"role": "user", "content": f["ask"]}], p,
                                        f["gold"], [g for g in golds_all if g != f["gold"]],
                                        n_base=rs["n_restored"])
                failed_total += failed
                if N == 100:                                     # the banked strict scorer rules
                    cls = "gold" if hit(recs[-1]["answer"], f["gold"]) else cls
                n_gold += cls == "gold"
                if not recs[-1]["geom_ok"] and recs[-1]["ptok"] is not None:
                    geom_void.append((N, p, f["i"]))
            per_pass.append(n_gold)
            print(f"[bank{N}] pass {p}: {n_gold}/{N} gold ({100 * n_gold / N:.1f}%)", flush=True)
        recalls = [r for r in recs if r.get("arm") == "recall" and r.get("rung") == N
                   and not r.get("retried")]
        recall_mean = sum(per_pass) / len(per_pass) / N
        # 3) leak arm: decoy installed, the BANK's asks re-asked (c3v3 semantics at scale)
        dl = decoy_facts() if N == 100 else dec[:N]
        conv_d = encode(dl, [], f"dec{N}", N)
        svd = slot(url, "save", f"c4_dec{N}_{a.tag}")
        sample = dl if N != 100 and a.leak_sample else fam       # bank asks, thinned for big rungs
        if N == 100:
            sample = fam
        if a.leak_sample and N != 100:
            sample = fam[::max(1, len(fam) // a.leak_sample)]
        leaks = 0
        for f in sample:
            rs = slot(url, "restore", f"c4_dec{N}_{a.tag}")
            cls, failed = ask_retry("leak", N, f"row{f['i'] + 1}" if N == 100 else f"nonce{f['i']}",
                                    conv_d + [{"role": "user", "content": f["ask"]}], 1,
                                    f["gold"], [d["gold"] for d in dl])
            failed_total += failed
            if N == 100:
                cls = "gold" if hit(recs[-1]["answer"], f["gold"]) else cls
            leaks += cls == "gold"
            if not recs[-1]["geom_ok"] and recs[-1]["ptok"] is not None:
                geom_void.append((N, "leak", f["i"]))
        leak_rate = leaks / max(len(sample), 1)
        print(f"[bank{N}] leak: {leaks}/{len(sample)} bank golds under the foreign install "
              f"(decoy n_saved={svd['n_saved']})", flush=True)
        # 4) plumbing sanity (fact+ask, one message, no bank): c3's read-path gate
        plumb = fam if N == 100 else fam[::max(1, len(fam) // 50)]
        p_ok = sum(ask_retry("plumb", N, f"row{f['i'] + 1}" if N == 100 else f"nonce{f['i']}",
                             [{"role": "user", "content":
                               f"{f['fact']} Given only that, answer.\n\n{f['ask']}"}],
                             0, f["gold"], [])[0] == "gold" for f in plumb)
        # 5) cost ledger (reported, never gated) + displacement headroom note
        secs = [r["secs"] for r in recalls if r.get("secs") is not None]
        ptoks = [r["ptok"] for r in recalls if r.get("ptok") is not None]
        led = {"rung": N, "recall_per_pass": per_pass, "recall_mean": round(recall_mean, 4),
               "distractors": sum(1 for r in recalls if r["cls"] == "distractor"),
               "idks": sum(1 for r in recalls if r["cls"] == "idk"),
               "cold_contam": cold_contam, "prefix_tok": sv["n_saved"],
               "encode_s": encode_s, "save_ms": sv["timings"].get("save_ms"),
               "restore_ms_first": (first_restore or {}).get("timings", {}).get("restore_ms"),
               "leak": f"{leaks}/{len(sample)}", "leak_n": len(sample),
               "leak_rate": round(leak_rate, 4), "plumb": f"{p_ok}/{len(plumb)}",
               "ask_secs_mean": round(statistics.mean(secs), 2) if secs else None,
               "ask_secs_p95": round(statistics.quantiles(secs, n=20)[18], 2)
                               if len(secs) >= 20 else None,
               "ptok_mean": round(statistics.mean(ptoks)) if ptoks else None}
        try:      # displacement: one memory-ON ask (block + prod standing ride THIS box)
            st1, r1 = http(url + "/v1/chat/completions",
                           {"model": model, "messages": [{"role": "user", "content":
                            "According to the bank, what is the capital of France? Answer briefly."}],
                            "temperature": a.temp, "reasoning_effort": "off", "max_tokens": 16,
                            "stream": False}, headers={"X-Strata-Memory-Scope": "default"},
                           timeout=120)
            led["on_ask_ptok"] = ((r1 or {}).get("usage") or {}).get("prompt_tokens") \
                if st1 == 200 else None
        except Exception as e:
            led["on_ask_ptok"] = f"ERR {type(e).__name__}"
        led["reseed_prompt_n"] = next((r["prompt_n"] for r in recs if r.get("arm") == "reseed"
                                       and r.get("rung") == N), None)
        ledger[N] = led
        print(f"[bank{N}] ledger {json.dumps(led)}", flush=True)
        if not a.keep_files:                                     # slot_retain is 6: stay light
            for nm in (f"c4_bank{N}_{a.tag}", f"c4_dec{N}_{a.tag}"):
                try:
                    (SESSIONS / f"{nm}.bin").unlink(missing_ok=True)
                except OSError:
                    pass

    # ---- verdict, mechanically per C4_PRE_REG.md -------------------------------------------
    p100 = ledger.get(100, {}).get("leak_rate")
    lines = []
    for N in rungs:
        led = ledger[N]
        lg = ""
        if p100 is not None and N != 100 and led["leak_rate"] > p100 and led["leak_n"]:
            pv = binom_ge(round(led["leak_rate"] * led["leak_n"]), led["leak_n"], p100)
            lg = f" | LEAK {'MISS' if pv < 0.05 else 'within allowance'} (p={pv:.3f})"
        lines.append(f"  rung {N}: recall {' '.join(str(x) for x in led['recall_per_pass'])} "
                     f"mean {led['recall_mean']:.3f} -> "
                     f"{'PASS' if led['recall_mean'] >= 0.95 else 'FAIL'}{lg}; distractors "
                     f"{led['distractors']}, idk {led['idks']}, plumb {led['plumb']}; prefix "
                     f"{led['prefix_tok']} tok, encode {led['encode_s']} s, save {led['save_ms']} "
                     f"ms, restore {led['restore_ms_first']} ms, reseed prompt_n "
                     f"{led['reseed_prompt_n']}; ask {led['ask_secs_mean']} s mean / "
                     f"{led['ask_secs_p95']} p95, ptok {led['ptok_mean']}, ON-ask "
                     f"{led['on_ask_ptok']}")
    passes = [N for N in rungs if ledger[N]["recall_mean"] >= 0.95]
    fails = [N for N in rungs if ledger[N]["recall_mean"] < 0.95]
    if not fails:
        ceiling = ("no ceiling found at " + str(max(rungs)) + " - the next rung is C4-v2's to pick")
    else:
        last = max([N for N in passes if N < min(fails)], default=None)
        ceiling = (f"ceiling declared between {last} and {min(fails)}" if last is not None
                   else "even the first rung failed - the instrument is in question, not the bank")
    void = [f"geometry VOID on {len(geom_void)} ask(s): {geom_void[:4]}"] if geom_void else []
    if control and ledger.get(100) and ledger[100]["recall_mean"] < 0.85:
        void.append(f"control rung (100) at {ledger[100]['recall_mean']:.3f} < 0.85 - this era"
                    " cannot reproduce C3's known-clean number; compare before believing rungs")
    for N in rungs:                                              # c3's plumbing gate, per rung
        pl = ledger[N]["plumb"]
        ok, tot = (int(x) for x in pl.split("/"))
        if tot and ok / tot < 0.9:
            void.append(f"plumbing {pl} at rung {N} (< 90%): the read path itself is sick")
    if failed_total > 0.05 * sum(N * a.repeats for N in rungs):
        void.append(f"{failed_total} transport-dead asks (> 5%): the box was not healthy")
    head = (f"\nC4 verdict @{now()} tag={a.tag} rungs={rungs} repeats={a.repeats} "
            f"temp={a.temp} channel=off/memory-muted geom_delta={ddelta:+d}"
            f"{' | SMOKE - plumbing proof, not a rung verdict' if a.smoke else ''}\n")
    tail = (head + "\n".join(lines) + f"\n  {ceiling}"
            + (f"\n  LEAK BASELINE = rung-100 rate {p100}" if p100 is not None
               else "\n  LEAK BASELINE: rung 100 not run - leak gates idle this run")
            + (f"\n  VOID: {'; '.join(void)}" if void else "\n  all guards green") + "\n")
    print(tail)
    (BENCH / f"c4_verdict_{a.tag}.txt").write_text(
        "\n".join(f"[{r.get('arm')}/{r.get('rung')}p{r.get('pass')}] {r.get('name')} "
                  f"cls={r.get('cls')} ptok={r.get('ptok')}/{r.get('want_ptok')} "
                  f"geom={r.get('geom_ok')} ride={r.get('ride_ok')} cache_n={r.get('cache_n')} "
                  f"prompt_n={r.get('prompt_n')} base={r.get('n_base')} :: {r.get('answer', '')}"
                  for r in recs if r.get("arm") != "reseed" or True) + tail)
    return 1 if void else 0


if __name__ == "__main__":
    sys.exit(main())
