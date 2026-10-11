#!/usr/bin/env python3
"""spikes/cartridge/c3_run.py - the C3 100-fact scaling verdict driver (venue + bands
PRE-REGISTERED in C3_PRE_REG.md - read that first; do not edit it after this runs).

Shape (channel: reasoning_effort off + temperature 0.8 + X-Strata-Memory: off on EVERY request,
max_tokens 24, one fresh conversation per ask, geometry guard per ask):

  bank       = all 100 Gate-1 facts ("{question} Answer: {gold}.") as 100 user messages in CSV
               order, each acked by the model (cap 16 tok, replayed verbatim), saved ONCE;
  c3b        = 3 passes x 100 asks: restore -> the identical 201-message list + the bare CSV
               question as the final turn (the product shape; ride guard per ask against the
               restore's n_restored);
  plumbing   = 100 asks: "{fact} Given only that, answer.\n\n{question}" (the read-path gate);
  cold       = 100 asks: the bare question (all 100 rows are cold-UNKNOWN by selection - any
               gold is contamination evidence);
  decoy_real = 100 asks: a decoy bank of 100 unique Smokeport nonce facts installed, every real
               question asked (any real gold = the real bank persisting through an install swap);
  decoy_own  = 10 asks (every 10th), recorded bonus, never scored.

Scoring imports spikes/window/gate1_driver.hit - the EXACT strict word-boundary scorer that
banked tier-3's 100/100 (FAA variants, dates, st/period, ordinals). Classes: gold > distractor
(hit on another row's gold - the interference metric) > idk > other. Bands: PASS-AT-SCALE
(>= 95/100 in >= 2 of 3 passes) / DEGRADE (best 85-94) / FAIL-AT-SCALE (best < 85) /
INSTALL-BROKEN (any c3b or decoy ride-guard failure) / VOID (plumbing < 100/100, any cold or
decoy leak, any geometry VOID). Robustness per the 2026-10-11 audit: 5xx/transport recorded
per-ask, never a crash; timeouts 960 s; --repeats < 3 refused; VOID exits 1.

Usage: python3 spikes/cartridge/c3_run.py [--url http://127.0.0.1:8005] [--temp 0.8]
           [--repeats 3] [--tag NAME] [--slack 8] [--dry-run]
Writes state/c3_run_<tag>.jsonl (append) + state/c3_verdict_<tag>.txt.
"""
from __future__ import annotations
import argparse, collections, datetime, json, re, sys, time, urllib.error, urllib.request as U
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "spikes" / "d2_dissect"))
sys.path.insert(0, str(ROOT / "spikes" / "window"))
from venue_render import load_tokenizer, load_template, live_render  # noqa: E402
from gate1_driver import rows, hit  # the banked scorer itself - imported, not reimplemented  # noqa: E402

LOG = ROOT / "strata-dev.log"
IDK = ("idk", "i don't know", "i do not know", "unknown", "don't know", "no answer", "")
# decoy bank: 100 UNIQUE nonce facts, zero shared entities with the real questions (stems avoid
# every real-gold substring, e.g. no "marsh" - a real gold family); kind cycles 4 ways, town and
# gold carry the row index so every decoy fact is individually askable (decoy_own bonus).
D_KINDS = ("mayor", "bridge", "brew", "radio mast")
D_STEMS = ("Fenwick", "Draymoor", "Ossuland", "Plenwick", "Netheath", "Vamlstad", "Kirkfen",
           "Thornby")


def decoy_facts():
    out = []
    for i in range(100):
        kind, stem = D_KINDS[i % 4], D_STEMS[i % 8]
        town, gold = f"{stem}{i}", f"Zivvleax{i}-q{chr(97 + i % 26)}"
        out.append({"n": i + 1, "kind": kind, "town": town, "gold": gold,
                    "fact": f"The {kind} of {town} is {gold}.",
                    "ask": f"According to the bank, what is the {kind} of {town}? "
                           f"Answer with just the name."})
    return out


def http(url, payload=None, headers=None, timeout=960):
    """-> (status, parsed_body); HTTPError + transport errors RETURNED, never raised (audit
    2026-10-11: one transient 503 must not lose the verdict artifact of a 40-minute run)."""
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
    except OSError as e:
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
    if not LOG.exists():
        return []
    with LOG.open("rb") as f:
        f.seek(off)
        new = f.read().decode("utf-8", "replace")
    return [ln.strip() for ln in new.splitlines()
            if re.search(r"\b(RESUME|REUSED) \d+|= \d+ reused \+ \d+ read", ln)][:6]


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
        raise SystemExit("--repeats >= 3 is mandatory protocol (C3_PRE_REG.md)")

    tok = load_tokenizer()
    tpl = load_template()
    R = rows()
    assert len(R) == 100, f"expected the 100-row Gate-1 set, got {len(R)}"
    DECOY = decoy_facts()
    golds = [r["expected_answer_contains"] for r in R]

    def question(i):
        return R[i]["question"]

    def fact(i):
        return f"{R[i]['question']} Answer: {R[i]['expected_answer_contains']}."

    def classify(ans, i):
        if hit(ans, golds[i]):
            return "gold", i
        for j in range(100):
            if j != i and hit(ans, golds[j]):
                return "distractor", j
        if ans.lower().strip().rstrip(".!") in IDK:
            return "idk", None
        return "other", None

    # ---- dry-run: render everything, contact nothing ----------------------------------------
    if a.dry_run:
        bank = []
        for i in range(100):
            bank.append({"role": "user", "content": fact(i)})
            bank.append({"role": "assistant", "content": "OK."})   # placeholder ack
        ids, _ = live_render(tok, tpl, bank, "off")
        est = []
        for i in (0, 1, 2):
            q = [{"role": "user", "content": question(i)}]
            p = [{"role": "user", "content": f"{fact(i)} Given only that, answer.\n\n{question(i)}"}]
            for arm, msgs in (("cold", q), ("plumbing", p), ("c3b", bank + q)):
                ids2, _ = live_render(tok, tpl, msgs, "off")
                est.append({"arm": arm, "row": i + 1, "expect_ptok": len(ids2),
                            "tail_tokens": [tok.decode([t]) for t in ids2[-3:]]})
        print(json.dumps({"bank_render_placeholder_acks": len(ids),
                          "bank_file_estimate_MB_at_1_2GB_per_63k": round(len(ids) * 1200 / 63000, 1),
                          "asks_live": 100 + 100 + 100 * a.repeats + 100 + 10,
                          "encode_turns": 200,
                          "slot_ops": 100 * a.repeats + 100 + 10 + 2,
                          "samples": est}, indent=1))
        print("dry-run: no contact; bank ground truth = save n_saved at run time (acks sampled "
              f"at temp {a.temp}, so the placeholder render is an estimate)")
        return 0

    # ---- live run ---------------------------------------------------------------------------
    st, health = http(url + "/health")
    if st != 200:
        raise SystemExit(f"/health -> {st}: {json.dumps(health)[:160]} - no run")
    model = health.get("model") or "strata"
    print(f"health {health.get('status')} model={model} max_context={health.get('max_context')} "
          f"tag={tag} channel=off/temp{a.temp}/memory-muted repeats={a.repeats}")

    (HERE / "state").mkdir(parents=True, exist_ok=True)
    out_jsonl = HERE / f"state/c3_run_{tag}.jsonl"
    recs = []

    def ask(arm, name, msgs, row=None, n_base=None, log_evidence=False):
        ids, _ = live_render(tok, tpl, msgs, "off")
        body = {"model": model, "messages": msgs, "temperature": a.temp,
                "reasoning_effort": "off", "max_tokens": a.maxtok, "stream": False}
        off = log_mark() if log_evidence else 0
        t0 = time.time()
        st, r = http(url + "/v1/chat/completions", body, headers={"X-Strata-Memory": "off"})
        if st != 200:  # recorded per-ask (geom-VOID via ptok None); the run survives to the verdict
            ans = f"HTTP {st}: {json.dumps(r)[:110]}"
        else:
            ans = (((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        ptok = (r.get("usage") or {}).get("prompt_tokens")
        t = r.get("timings") or {}
        cache_n, prompt_n = t.get("cache_n"), t.get("prompt_n")
        geom_ok = ptok == len(ids)
        ride_ok = None
        if n_base is not None:
            ride_ok = (cache_n is not None and cache_n >= n_base - a.slack
                       and prompt_n is not None and prompt_n <= (len(ids) - n_base) + a.slack)
        cls, distractor_of = (classify(ans, row) if row is not None and geom_ok else
                              (("other", None) if row is not None else (None, None)))
        gold_hit = cls == "gold" if row is not None else None
        leak = arm in ("cold", "decoy_real") and bool(cls == "gold") if row is not None else False
        rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "tag": tag, "pass": cur_pass, "arm": arm, "name": name, "answer": ans,
               "cls": cls, "distractor_of_row": distractor_of, "ptok": ptok,
               "expect_ptok": len(ids), "geom_ok": geom_ok, "cache_n": cache_n,
               "prompt_n": prompt_n, "n_base": n_base, "ride_ok": ride_ok, "leak": leak,
               "secs": round(time.time() - t0, 2)}
        if log_evidence:
            rec["engine_log"] = log_since(off)
        recs.append(rec)
        with out_jsonl.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        g = "geom-OK" if geom_ok else f"GEOM {ptok}/{len(ids)}"
        rd = "" if ride_ok is None else (" ride-OK" if ride_ok else " RIDE-NO-GO")
        print(f"[p{cur_pass}/{arm}] {name}: {g}{rd} cache_n={cache_n} prompt_n={prompt_n} "
              f"-> {ans[:60]!r} [{cls}]")
        return rec

    def encode(facts, what):
        conv = []
        for i, f in enumerate(facts):
            conv.append({"role": "user", "content": f})
            st, r = http(url + "/v1/chat/completions",
                         {"model": model, "messages": conv, "temperature": a.temp,
                          "reasoning_effort": "off", "max_tokens": a.acktok, "stream": False},
                         headers={"X-Strata-Memory": "off"})
            if st != 200:  # audit discipline: a 5xx here would silently break fact->ack adjacency
                raise SystemExit(f"{what} encode turn {i + 1} -> {st}: {json.dumps(r)[:160]}")
            conv.append({"role": "assistant",
                         "content": (((r.get("choices") or [{}])[0].get("message") or {})
                                     .get("content") or "").strip()})
        return conv

    # bank: the whole 100-fact panel, one encode walk, one save
    cur_pass = 0
    t0 = time.time()
    bank = encode([fact(i) for i in range(100)], "bank")
    sv = slot(url, "save", f"c3_bank_{tag}")
    n_bank = sv["n_saved"]
    print(f"bank: 100 facts encoded in {time.time() - t0:.0f} s, n_saved={n_bank} "
          f"n_written={sv['n_written']} B save_ms={sv['timings']['save_ms']}")

    cur_pass = 1
    for i in range(100):  # read-path gate per fact
        ask("plumbing", f"row{i + 1}",
            [{"role": "user", "content": f"{fact(i)} Given only that, answer.\n\n{question(i)}"}],
            row=i)
    for i in range(100):  # cold: every row is cold-unknown by selection - any gold = leak
        ask("cold", f"row{i + 1}", [{"role": "user", "content": question(i)}], row=i)

    for cur_pass in range(1, a.repeats + 1):
        for i in range(100):
            rs = slot(url, "restore", f"c3_bank_{tag}")
            ask("c3b", f"row{i + 1}", bank + [{"role": "user", "content": question(i)}],
                row=i, n_base=rs["n_restored"], log_evidence=(i == 0))

    # decoy bank: install-swap contamination test at 100-fact scale
    t0 = time.time()
    dconv = encode([d["fact"] for d in DECOY], "decoy")
    sv = slot(url, "save", f"c3_decoy_{tag}")
    print(f"decoy bank: encoded in {time.time() - t0:.0f} s, n_saved={sv['n_saved']}")
    cur_pass = a.repeats + 1
    for i in range(100):
        rs = slot(url, "restore", f"c3_decoy_{tag}")
        ask("decoy_real", f"row{i + 1}", dconv + [{"role": "user", "content": question(i)}],
            row=i, n_base=rs["n_restored"])
    for i in range(0, 100, 10):  # bonus only: the decoy bank must answer ITS OWN questions
        rs = slot(url, "restore", f"c3_decoy_{tag}")
        rec = ask("decoy_own", f"dec{DECOY[i]['n']}",
                  dconv + [{"role": "user", "content": DECOY[i]["ask"]}], n_base=rs["n_restored"])
        rec["decoy_gold_hit"] = DECOY[i]["gold"].lower() in rec["answer"].lower()

    # ---- verdict, mechanically per C3_PRE_REG.md --------------------------------------------
    def scored(arm):
        return [r for r in recs if r["arm"] == arm and r["geom_ok"]]

    plumbing = scored("plumbing")
    plumb_hits = sum(1 for r in plumbing if r["cls"] == "gold")
    cold_leaks = [r for r in scored("cold") if r["leak"]]
    dreal = scored("decoy_real")
    d_leaks = [r for r in dreal if r["leak"]]
    c3b = scored("c3b")
    geom_void = [r for r in recs if not r["geom_ok"]]
    by_pass = {p: sum(1 for r in c3b if r["pass"] == p and r["cls"] == "gold")
               for p in range(1, a.repeats + 1)}
    dist_pass = {p: sum(1 for r in c3b if r["pass"] == p and r["cls"] == "distractor")
                 for p in range(1, a.repeats + 1)}
    coll = collections.Counter()
    for r in c3b:
        if r["cls"] == "distractor":
            coll[f"row{r['name'][3:]}->row{r['distractor_of_row'] + 1}"] += 1
    rides = c3b + dreal
    ride_all_ok = bool(c3b) and all(r["ride_ok"] for r in rides)
    best = max(by_pass.values()) if by_pass else -1
    band_pass = sum(1 for v in by_pass.values() if v >= 95) >= 2

    if len(plumbing) < 100 or plumb_hits < len(plumbing):
        verdict = (f"VOID - plumbing gate {plumb_hits}/{len(plumbing)} (100/100 registered): "
                   "this boot supports no C3 verdict")
    elif cold_leaks or d_leaks:
        verdict = ("VOID - control leak: " + str([{"arm": r["arm"], "name": r["name"],
                                                   "answer": r["answer"][:60]}
                                                  for r in cold_leaks + d_leaks][:6]))
    elif geom_void:
        verdict = (f"VOID - geometry guard failed on {len(geom_void)} ask(s): "
                   f"{[r['name'] + '/' + r['arm'] for r in geom_void][:6]}")
    elif not ride_all_ok:
        bad = [r for r in rides if not r["ride_ok"]]
        verdict = (f"INSTALL-BROKEN - ride guard failed on {len(bad)}/{len(rides)} asks "
                   f"(cache_n {bad[0]['cache_n']} vs base {bad[0]['n_base']} typical): install "
                   "path suspect, not a memory verdict")
    elif band_pass:
        verdict = (f"PASS-AT-SCALE - c3b gold per-pass {by_pass} (band >= 95 in >= 2 passes), "
                   f"distractors per-pass {dist_pass}, 0 leaks, every ride green: the cartridge "
                   "holds the whole 100-fact bank with ~50 re-read tokens/ask and NO selector - "
                   "head-to-head vs tier-3's 100/100 at ~485 injected tokens/ask")
    elif 85 <= best <= 94:
        verdict = (f"DEGRADE - best pass {best}/100 (band 85-94): the channel works but the "
                   f"interference tax is real (distractors per-pass {dist_pass}, top collisions "
                   f"{coll.most_common(6)}); the marking roadmap (layer 2: tagged per-domain "
                   "banks) earns its keep")
    elif best < 85:
        verdict = (f"FAIL-AT-SCALE - best pass {best}/100: the raw whole-state channel "
                   f"saturates at 100 facts (distractors {dist_pass}, top collisions "
                   f"{coll.most_common(6)}); layered banks are mandatory before any product claim")
    else:
        verdict = (f"MECHANISM-SENSITIVE - per-pass {by_pass}: >= 95 in only one pass (high "
                   "variance); recorded, stop, no venue-hunting without a new pre-registration")

    own = [r for r in recs if r["arm"] == "decoy_own"]
    secs = [r["secs"] for r in c3b]
    tail = (f"\nC3 verdict @{datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')} "
            f"tag={tag} channel=off/temp{a.temp}/memory-muted repeats={a.repeats}\n"
            f"  plumbing {plumb_hits}/{len(plumbing)} | cold leaks {len(cold_leaks)}/100 | "
            f"decoy-real leaks {len(d_leaks)}/100 | c3b gold per-pass {by_pass} best={best} | "
            f"distractors per-pass {dist_pass} top-collisions {coll.most_common(5)} | "
            f"rides green={ride_all_ok} ({len(rides)} guarded asks) | geometry VOID {len(geom_void)} | "
            f"reuse-summary captured on {sum(1 for r in recs if r.get('engine_log'))}/{a.repeats} "
            f"marked c3b asks | decoy-own bonus "
            f"{sum(1 for r in own if r.get('decoy_gold_hit'))}/{len(own)} | "
            f"c3b ask seconds avg {round(sum(secs) / len(secs), 2) if secs else 'n/a'} | "
            f"bank n_saved={n_bank}\n"
            f"  {verdict}\n")
    print(tail)
    (HERE / f"state/c3_verdict_{tag}.txt").write_text(
        "\n".join(f"[p{r['pass']}/{r['arm']}] {r['name']} cls={r['cls']} dist={r['distractor_of_row']} "
                  f"ptok={r['ptok']}/{r['expect_ptok']} cache_n={r['cache_n']} "
                  f"prompt_n={r['prompt_n']} base={r['n_base']} ride={r['ride_ok']} "
                  f"leak={r['leak']} :: {r['answer']}" for r in recs) + tail)
    return 1 if verdict.startswith(("VOID", "INSTALL")) else 0


if __name__ == "__main__":
    sys.exit(main())
