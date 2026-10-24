#!/usr/bin/env python3
"""spikes/cartridge/c5_run.py - the bank-composition venue (contract: C5_PRE_REG.md - read it
first; the countersign gate: NO ask is generated unless --i-countersigned is passed, so the
owner's pen literally precedes the first C5 token; --dry-run plans with zero engine calls).

The one question (C5): when N shelf-cartridges are COMPOSED into one standing conversation,
does the slot ride it as a stable prefix AND does the model still answer across ALL N shelves -
or does composition dilute recall the way the numeric arm's cliff did? The answer decides
whether the MAM bank is buildable on the engine as-is (text cartridges, composed; the MEM/CAT
index as its card catalog) or caps at rung k-1 while many-cartridges-per-request waits for
Spike B.

Shape (fixed by the pre-registration; one bank growing, never four different banks):
  snapshot    = the live prod store read ONCE at start: shelved rows, scopes user+default,
                MEM/CAT excluded (the index is not a drawer), shelves ordered exactly as the
                browse map orders them (size desc, mark asc);
  rungs       = R1/R5/R10/R20 = first N shelves x min(20, drawer size) NEWEST facts each -
                nested by construction, so every probe asked at a small rung is asked again
                verbatim at every larger rung. (Pre-signature amendment, printed in the plan:
                the store carries only ~6 shelves of >=20 rows, so R20 is 20 shelves x UP TO
                20, measured and printed, never padded with invented facts.)
  seed        = one user message per shelf block - call-number header "[MARK]" + bullets -
                acked by the engine itself (the c3v3 law: the ack is whatever the model wrote,
                replayed verbatim in every continuation; that is what makes the prefix
                byte-exact), saved once per rung;
  probes      = 2 direct per shelf, chosen deterministically: CUE = a token with df==1 in the
                bank, GOLD = a second df==1 token from the SAME fact; the question quotes the
                cue, the answer must carry the gold - no circularity, the question never
                echoes the gold. idk-rule voice, final-line scoring (M3c's criterion). The
                rung's number = MIN across shelves: the weakest drawer decides.
  aggregation = 5 cross-shelf questions fixed at plan time from the snapshot (two unique cues,
                the answer must name BOTH shelf marks) - the multi-session class in miniature,
                deterministic judge, full records kept for any later re-score;
  control     = one muted ask per rung per rep: the first probe's question with NO bank text
                in the conversation - must idk (M3c's law re-probed at bank scale). A gold
                there is a LEAK: exit 2, the venue stops.
  mechanics   = every probe rides a FRESH restore (the c3v3 pattern): restore -> ask, so the
                probe is the first byte-exact continuation; the ride guard reads the engine's
                bookkeeping from the response (cache_n >= n_restored - slack AND prompt_n <=
                200 - a probe that had to re-read the bank is not riding it). Once per rung a
                turn-2 follow-up goes out WITHOUT restoring, proving the prefix survives a
                second conversational turn. restore_ms rides every restore response; engine
                logs are never read (the venue-day scar).

Ends by restoring the live standing cartridge: the slot returns to the shop as found.

Run LAST in a window, backgrounded, nothing else touching the port (the M3c rule - production
chats during the run are SLOW but correct: their prompts re-prefill the bank every turn).
Writes state/c5_run_<tag>.jsonl + state/c5_run_<tag>.metrics.json + state/c5_verdict_<tag>.txt.

Usage: python3 spikes/cartridge/c5_run.py --dry-run
       python3 spikes/cartridge/c5_run.py --i-countersigned [--repeats 3] [--tag NAME]
"""
from __future__ import annotations
import argparse, collections, datetime, json, re, statistics, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE), str(ROOT / "spikes" / "d2_dissect"), str(ROOT / "spikes" / "window")):
    if p not in sys.path:
        sys.path.insert(0, p)
from serve.memory import STOP, CAT_CLASS, attach          # the store's own word law
from strata_control.datafolder import data_dir            # the single data folder law (m4 pattern)
from c3_run import http, slot                             # the venue-hardened machinery

STATE = HERE / "state"
RUNGS = (("R1", 1), ("R5", 5), ("R10", 10), ("R20", 20))
ANSWER_RULE = "Answer in a few words from what you know. If you do not know, reply exactly: idk"
TOKEN = re.compile(r"[a-z0-9][a-z0-9.+#\-]{2,}")
AGG_PAIRS = ((0, 1), (2, 4), (3, 0), (1, 3), (4, 2))   # indices into the top-5 shelves


def toks(t: str) -> list[str]:
    """Probe tokens with the catalog's word law (STOP-filtered, >=3 chars, no bare numbers):
    the bank, its index and its probes all speak one dialect. Trailing punctuation is cut -
    a gold of 'drafting.' demands the answer END the sentence at that exact spot, which is a
    typography test, not a memory test (caught in the first dry run)."""
    out = []
    for w in TOKEN.findall(t.lower()):
        w = w.strip(".+#-")
        if len(w) >= 3 and w not in STOP and not w.replace(".", "").isdigit():
            out.append(w)
    return out


def final_line(s: str) -> str:
    return (s or "").strip().split("\n")[-1].lower()


def snapshot(db: str) -> list:
    """One read, map order: [(mark, [(id, text) newest first])] over scopes user+default,
    MEM/CAT excluded. The SAME snapshot seeds every rung and chooses every probe, so the
    ladder is one bank growing - never four banks compared."""
    con = attach(db, autocommit=True)
    shelves: dict[str, list] = {}
    for mid, cls, txt in con.execute(
            "select id, class, text from memories where invalid_at is null"
            " and scope = any(%s) and coalesce(class, '') not in ('', %s)"
            " order by class, created_at desc, id desc", (["user", "default"], CAT_CLASS)):
        shelves.setdefault(cls, []).append((mid, txt))
    con.close()
    return sorted(shelves.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def plan(shelves: list, per: int = 20) -> dict:
    """Fix bank, probes and aggregation BEFORE any ask (the pre-registration's 'composed from
    the same real facts', deterministically). Cue/gold pairs are chosen on the R20 pool, so
    they stay df==1 in every nested sub-rung - the same question means the same rarity at
    every rung."""
    rung_shelves = {n: [(m, [t for _i, t in rows[:per]]) for m, rows in shelves[:n]]
                    for n in (1, 5, 10, 20)}
    bank20 = [t for _m, fs in rung_shelves[20] for t in fs]
    df: collections.Counter = collections.Counter(w for f in bank20 for w in set(toks(f)))
    marks = {m for m, _f in rung_shelves[20]}
    mark_words = {p for m in marks for p in m.lower().split("/")}

    def pair(text: str):
        """First two unique tokens as (cue, gold): the question quotes the cue, the answer
        must carry the gold; the gold must not echo inside the question itself. CONTENT-WORD
        LAW: a token with a digit (a work-log date, a sha fragment) is rare but unreproducible
        in free speech - a shelf probed on one false-misses even with perfect composition, so
        digit tokens label only when nothing else is unique (and the probe says so)."""
        uniq = [w for w in toks(text) if df[w] == 1 and w not in mark_words]
        pool = [w for w in uniq if not any(c.isdigit() for c in w)]
        marked = not pool and len(uniq) >= 2
        pool = pool or uniq
        if len(pool) < 2:
            return None
        cue = pool[0]
        gold = next((w for w in pool[1:] if w not in cue and cue not in w), None)
        if gold is None or gold in f'your notes mention "{cue}". what do they say about it?':
            return None
        return cue, gold, marked

    probes: dict[str, list] = {}
    for mark, facts in rung_shelves[20]:
        got = []
        for f in facts:
            pr = pair(f)
            if pr:
                got.append({"mark": mark, "cue": pr[0], "gold": pr[1], "fact": f[:140],
                            "digit_note": pr[2]})
            if len(got) == 2:
                break
        probes[mark] = got

    by_mark = dict(rung_shelves[20])

    def unique_in(mark: str):
        for f in by_mark[mark]:
            for w in toks(f):
                if df[w] == 1 and w not in mark_words:
                    return w
        return None

    top5 = [m for m, _f in rung_shelves[5]]
    agg = []
    for ia, ib in AGG_PAIRS:
        if ia < len(top5) and ib < len(top5):
            ca, cb = unique_in(top5[ia]), unique_in(top5[ib])
            if ca and cb and ca != cb:
                agg.append({"a": top5[ia], "b": top5[ib], "ca": ca, "cb": cb})
    return {"rungs": rung_shelves, "probes": probes, "agg": agg,
            "sizes": {f"R{n}": [len(rung_shelves[n]), sum(len(f) for _m, f in rung_shelves[n])]
                      for n in (1, 5, 10, 20)}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="C5 bank-composition venue (contract in C5_PRE_REG.md)")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--model", default="m")
    ap.add_argument("--db", default=str(data_dir(ROOT) / "memory-eval"),
                    help="store directory (one read at start; the m4 default, data_dir law - "
                         "attach STARTS a cluster where no store lives, so a wrong path is a "
                         "stray postmaster, not an error: caught by the first dry run)")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--slack", type=int, default=8)
    ap.add_argument("--acktok", type=int, default=24)
    ap.add_argument("--maxtok", type=int, default=60)
    ap.add_argument("--tag", default=datetime.datetime.now().strftime("%H%M%S"))
    ap.add_argument("--dry-run", action="store_true", help="plan only: zero engine calls")
    ap.add_argument("--i-countersigned", action="store_true",
                    help="the owner's pen precedes the first ask (contract gate)")
    a = ap.parse_args(argv)
    url = a.url.rstrip("/")
    STATE.mkdir(exist_ok=True)

    shelves = snapshot(a.db)
    p = plan(shelves)
    print(f"[c5] snapshot: {len(shelves)} shelved drawers; rungs [shelves, facts]: "
          + ", ".join(f"{k}={v[0]}/{v[1]}" for k, v in p["sizes"].items()))
    for mark, got in p["probes"].items():
        for pr in got:
            print(f"  probe [{mark}] cue '{pr['cue']}' -> gold '{pr['gold']}"
                  f"{' [digit-token fallback]' if pr['digit_note'] else ''}' :: {pr['fact'][:70]}")
        if len(got) < 2:
            print(f"  NOTE [{mark}] only {len(got)} valid probe(s): the min gate counts what exists")
    for q in p["agg"]:
        print(f"  agg   [{q['a']} x {q['b']}] cues '{q['ca']}' / '{q['cb']}'")
    if a.dry_run:
        (STATE / f"c5_plan_{a.tag}.json").write_text(json.dumps(
            {"tag": a.tag, "sizes": p["sizes"], "probes": p["probes"], "agg": p["agg"]}, indent=1))
        print(f"[c5] dry run - plan written to state/c5_plan_{a.tag}.json, engine untouched")
        return 0
    if not a.i_countersigned:
        raise SystemExit("[c5] UNSIGNED: C5_PRE_REG.md awaits the owner's countersign before any "
                         "C5 ask. Use --dry-run to plan, --i-countersigned when the pen has spoken.")
    # the venue's own plan rides with its record: which probe came from which fact, from which
    # snapshot - the dry-run plan was a rehearsal; THIS is the provenance of what actually ran.
    (STATE / f"c5_plan_{a.tag}.json").write_text(json.dumps(
        {"tag": a.tag, "sizes": p["sizes"], "probes": p["probes"], "agg": p["agg"]}, indent=1))

    out = STATE / f"c5_run_{a.tag}.jsonl"

    def rec(obj):
        obj = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "tag": a.tag, **obj}
        with out.open("a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def get(path, timeout=30):
        st, r = http(url + path, None, timeout=timeout)
        if st != 200:
            raise SystemExit(f"{path} -> {st}: {json.dumps(r)[:160]}")
        return r

    cart = get("/memory?limit=1")["stats"].get("cartridges") or {}
    if cart.get("state") == "pending":                     # the m3c warm-up: be the request
        http(url + "/v1/chat/completions",
             {"model": a.model, "max_tokens": 8, "temperature": 0.8,
              "messages": [{"role": "user", "content": "warm the cartridge"}]})
        cart = get("/memory?limit=1")["stats"]["cartridges"]
    standing = cart.get("file")
    print(f"[c5] standing cartridge to restore at the end: {standing} (state={cart.get('state')}) "
          f"- M3c rule: nothing else touches the port")

    def ask(msgs, kind, name, mark="", gold=None, n_base=None, guarded=True, scorer=None):
        t0 = time.time()
        st, r = http(url + "/v1/chat/completions",
                     {"model": a.model, "messages": msgs, "temperature": 0.8,
                      "reasoning_effort": "off", "max_tokens": a.maxtok, "stream": False},
                     headers={"X-Strata-Memory": "off"})
        ans = ((((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
               if st == 200 else f"HTTP {st}: {json.dumps(r)[:110]}")
        t = r.get("timings") or {}
        cache_n, prompt_n = t.get("cache_n"), t.get("prompt_n")
        hit = (bool(scorer(ans)) if scorer else
               (gold is not None and gold.lower() in final_line(ans)))
        says_idk = "idk" in final_line(ans)
        ride = None if not guarded else (cache_n is not None and cache_n >= n_base - a.slack
                                         and prompt_n is not None and prompt_n <= 200)
        rec({"kind": kind, "mark": mark, "name": name, "answer": ans[:240], "hit": hit,
             "idk": says_idk, "cache_n": cache_n, "prompt_n": prompt_n, "n_base": n_base,
             "ride_ok": ride, "ptok": (r.get("usage") or {}).get("prompt_tokens"),
             "secs": round(time.time() - t0, 2)})
        print(f"  [{kind}] {mark or name[:20]:<22} {'GOLD' if hit else 'idk ' if says_idk else 'miss'}"
              f"{'' if ride is None else '/RIDE-' + ('OK' if ride else 'NO')} "
              f"cache={cache_n} prompt={prompt_n} {round(time.time() - t0, 1)}s")
        return {"answer": ans, "hit": hit, "idk": says_idk, "ride_ok": ride,
                "secs": round(time.time() - t0, 2), "ptok": (r.get("usage") or {}).get("prompt_tokens")}

    def encode(blocks, rung, m):
        """c3v3's shape: the engine acks every block and its OWN acks ride every later
        continuation - byte-exactness is the price of a prefix that rides (C0's law)."""
        conv, t0 = [], time.time()
        r = None
        for blk in blocks:
            conv.append({"role": "user", "content": blk})
            st, r = http(url + "/v1/chat/completions",
                         {"model": a.model, "messages": conv, "temperature": 0.8,
                          "reasoning_effort": "off", "max_tokens": a.acktok, "stream": False},
                         headers={"X-Strata-Memory": "off"})
            if st != 200:
                raise SystemExit(f"{rung} encode -> {st}: {json.dumps(r)[:160]}")
            conv.append({"role": "assistant",
                         "content": (r["choices"][0]["message"].get("content") or "").strip()})
        sv = slot(url, "save", f"c5_{rung}_{a.tag}.bin")
        m["seed_secs"] = round(time.time() - t0, 1)
        m["seed_tokens"] = (r.get("usage") or {}).get("prompt_tokens")
        m["save_ms"] = (sv.get("timings") or {}).get("save_ms")
        m["n_written"] = sv.get("n_written")
        rec({"kind": "seed", "mark": rung, "blocks": len(blocks), "seed_secs": m["seed_secs"],
             "seed_tokens": m["seed_tokens"], "n_saved": sv.get("n_saved"),
             "n_written": sv.get("n_written"), "save_ms": m["save_ms"]})
        print(f"  [seed {rung}] {len(blocks)} blocks in {m['seed_secs']}s, "
              f"{m['seed_tokens']} standing tokens, save_ms={m['save_ms']}")
        return conv

    def med(vals):
        vals = sorted(v for v in vals if isinstance(v, (int, float)))
        return round(statistics.median(vals), 1) if vals else None

    mech = {rung: {"restore_ms": [], "asks": 0, "ride_fail": 0, "contaminated": 0,
                   "secs": [], "ptok": [], "turn2": None} for rung, _n in RUNGS}
    direct: dict = collections.defaultdict(list)     # (rung, mark) -> [hit per rep-probe]
    aggs: dict = collections.defaultdict(list)       # rung -> [(rep, hit)]
    ctrl: dict = collections.defaultdict(list)       # rung -> [idk per rep]
    leaks = 0

    for rung, n in RUNGS:
        marks_facts = p["rungs"][n]
        print(f"[c5] {rung}: {len(marks_facts)} shelves, "
              f"{sum(len(f) for _m, f in marks_facts)} facts standing")
        conv = encode([f"[{m}]\n" + "\n".join(f"- {t}" for t in fs) for m, fs in marks_facts],
                      rung, mech[rung])
        fname = f"c5_{rung}_{a.tag}.bin"
        probes = [pr for m, _f in marks_facts for pr in p["probes"][m]]
        m = mech[rung]
        first = None
        for rep in range(a.repeats):
            nb_last = None
            for pr in probes:
                rs = slot(url, "restore", fname)
                nb_last = rs.get("n_restored")
                m["restore_ms"].append((rs.get("timings") or {}).get("restore_ms"))
                q = f'Your notes mention "{pr["cue"]}". What do they say about it? {ANSWER_RULE}'
                res = ask(conv + [{"role": "user", "content": q}], "direct", pr["cue"],
                          mark=pr["mark"], gold=pr["gold"], n_base=nb_last)
                m["asks"] += 1
                if res["ride_ok"] is False:
                    # TRAFFIC CONTAMINATION, most likely (the M2d scar: one foreign ask in
                    # between invalidates the ride - an artifact, not a finding). Restore
                    # fresh and re-ask ONCE; the retry's answer is what scores, and a retry
                    # that ALSO fails the ride is a real mechanics failure, counted as such.
                    m["contaminated"] += 1
                    rs = slot(url, "restore", fname)
                    nb_last = rs.get("n_restored")
                    m["restore_ms"].append((rs.get("timings") or {}).get("restore_ms"))
                    res = ask(conv + [{"role": "user", "content": q}], "direct-retry", pr["cue"],
                              mark=pr["mark"], gold=pr["gold"], n_base=nb_last)
                m["secs"].append(res["secs"])
                m["ptok"].append(res["ptok"])
                if res["ride_ok"] is False:
                    m["ride_fail"] += 1
                direct[(rung, pr["mark"])].append(res["hit"])
                if first is None and rep == 0:
                    # turn-2 RIGHT HERE, before any other ask touches the tail: the follow-up
                    # is the strict extension C0 names, the cleanest claim that the prefix
                    # survives a second conversational turn (the next probe's restore wipes
                    # this tail - the venue stays clean per the c3v3 pattern).
                    first = (q, res["answer"])
                    rs2 = ask(conv + [{"role": "user", "content": q},
                                      {"role": "assistant", "content": res["answer"]},
                                      {"role": "user",
                                       "content": "Which shelf mark did that come from? "
                                                  "Reply with the mark only."}],
                              "turn2", "turn2-followup", n_base=nb_last)
                    m["turn2"] = rs2["ride_ok"]
                elif first is None:
                    first = (q, res["answer"])
            for qd in (p["agg"] if n >= 2 else []):
                rs = slot(url, "restore", fname)
                m["restore_ms"].append((rs.get("timings") or {}).get("restore_ms"))
                q = (f'Your notes sit in many shelves. One shelf mentions "{qd["ca"]}" and '
                     f'another mentions "{qd["cb"]}". Name both shelf marks: one line per cue, '
                     f'written as "cue -> SHELF-MARK". If you do not know, reply exactly: idk')
                lo = lambda ans, A=qd["a"], B=qd["b"]: (
                    A.lower() in ans.lower() and B.lower() in ans.lower()
                    and "idk" not in final_line(ans))
                res = ask(conv + [{"role": "user", "content": q}], "agg",
                          f'{qd["a"]} x {qd["b"]}', n_base=rs.get("n_restored"), scorer=lo)
                m["asks"] += 1
                if res["ride_ok"] is False:
                    m["contaminated"] += 1   # agg answers score from the TEXT either way; a
                    # broken ride here pollutes mechanics statistics only - recorded, never
                    # retried, so the curve's shape stays honest to one ask per question
                aggs[rung].append((rep, res["hit"]))
            rs = slot(url, "restore", fname)         # control: the same ask, NO bank text
            m["restore_ms"].append((rs.get("timings") or {}).get("restore_ms"))
            pr0 = probes[0] if probes else None
            if pr0:
                res = ask([{"role": "user", "content":
                            f'Your notes mention "{pr0["cue"]}". What do they say about it? {ANSWER_RULE}'}],
                          "control", "muted-no-bank", gold=pr0["gold"], guarded=False)
                if res["hit"]:
                    leaks += 1
                ctrl[rung].append(res["idk"])

    # the shop is returned as found: the live standing cartridge goes back on the slot
    if standing:
        rs = slot(url, "restore", standing)
        rec({"kind": "final_restore", "name": standing,
             "restore_ms": (rs.get("timings") or {}).get("restore_ms")})
        print(f"[c5] standing cartridge {standing} restored "
              f"({(rs.get('timings') or {}).get('restore_ms')} ms) - venue closed")

    summary, verdict_lines = {}, []
    for rung, n in RUNGS:
        m = mech[rung]
        per_shelf = {mark: round(sum(h) / len(h), 3) for (rg, mark), h in direct.items()
                     if rg == rung and h}
        rung_min = min(per_shelf.values()) if per_shelf else None
        by_rep = collections.Counter()
        for rep, hit in aggs[rung]:
            by_rep[rep] += int(hit)
        agg_best = max(by_rep.values()) if by_rep else None
        summary[rung] = {"shelves": len(p["rungs"][n]), "facts": p["sizes"][f"{rung}"][1],
                         "restore_ms_median": med(m["restore_ms"]), "asks": m["asks"],
                         "ride_fails": m["ride_fail"], "contaminated_retried": m["contaminated"],
                         "turn2_ride_ok": m["turn2"],
                         "min_per_shelf": rung_min, "per_shelf": per_shelf,
                         "agg_best": agg_best, "agg_by_rep": sorted(by_rep.values()),
                         "control_idk": f"{sum(ctrl[rung])}/{len(ctrl[rung])}",
                         "seed_secs": m.get("seed_secs"), "seed_tokens": m.get("seed_tokens"),
                         "save_ms": m.get("save_ms"), "n_written": m.get("n_written"),
                         "ask_secs_median": med(m["secs"]), "ask_ptok_median": med(m["ptok"])}
        print(f"[c5] {rung}: restore {summary[rung]['restore_ms_median']} ms, "
              f"rides {m['asks'] - m['ride_fail']}/{m['asks']} (contaminated-retried "
              f"{m['contaminated']}), min-per-shelf {rung_min}, "
              f"agg best {agg_best}, control idk {summary[rung]['control_idk']}")
    p1 = all(s["restore_ms_median"] is not None and s["restore_ms_median"] < 1000
             and s["ride_fails"] == 0 and s["turn2_ride_ok"] for s in summary.values())
    p2 = summary["R20"]["min_per_shelf"] is not None and summary["R20"]["min_per_shelf"] >= 0.90
    ag = [summary[r]["agg_best"] for r in ("R5", "R10", "R20")]
    p3 = (ag[2] is not None and ag[2] >= 3 and None not in ag
          and ag[0] <= ag[1] <= ag[2])
    p5 = leaks == 0
    verdict_lines = [
        f"P1 mechanics (restore < 1 s, every ask rides, turn-2 rides): {'PASS' if p1 else 'FAIL'}",
        f"P2 min-per-shelf >= 0.90 at R20 (THE gate): "
        f"{'PASS' if p2 else 'FAIL'} at {summary['R20']['min_per_shelf']}",
        f"P3 aggregation >= 3/5 at R20 and non-decreasing: "
        f"{'PASS' if p3 else 'FAIL'} (best {ag})",
        f"P4 cost curve roughly linear: reported, not gated (budget law)",
        f"P5 muted controls idk, zero leaks: {'PASS' if p5 else f'FAIL ({leaks} leaks)'}",
        "Decision rule (C5_PRE_REG): P2 holds at R20 -> bank buildable, MEM/CAT is its card "
        "catalog; falls at rung k -> product caps at k-1, many-per-request waits for Spike B.",
    ]
    metrics = {"tag": a.tag, "repeats": a.repeats, "sizes": p["sizes"], "rungs": summary,
               "leaks": leaks, "predictions": {"P1": p1, "P2": p2, "P3": p3, "P5": p5}}
    (STATE / f"c5_run_{a.tag}.metrics.json").write_text(json.dumps(metrics, indent=1))
    (STATE / f"c5_verdict_{a.tag}.txt").write_text("\n".join(verdict_lines) + "\n")
    print("\n".join(verdict_lines))
    if leaks:
        print(f"[c5] CONTROL LEAKED {leaks}x: gold appeared with no bank text - the venue stops "
              f"here until diagnosed (exit 2)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
