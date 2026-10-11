#!/usr/bin/env python3
"""spikes/window/gate1_rerun.py - T1 (plan §11): the Gate-1 CAPACITY re-run, scripted.

The original Gate-1 (2026-10-11 morning) failed on capacity, not readout: 10/100, because one
8-fact block was pinned to the FIRST user message of each paste conversation while recall_limit
sat at 8; the 8 facts a block DID carry answered 8/8 EXACT. This rerun asks the same 100 CSV
questions as FRESH single-turn chats (the cold baseline's shape) against the live dev engine and
scores two capacities:

  arm live8   - memory ON server-side: the running (:8005, pre-T1-code) server recalls its own
                top-8 for the question and injects it. The block is NOT sent by this script;
                the local in-process render of [system block + question] must still match
                usage.prompt_tokens - which is the proof that the server recalled exactly the
                8 facts this same store+code would rank (geometry guard, v2_run.py's pattern).
  arm local16 - proposed new capacity: block rendered HERE (same MemoryService code pointed at
                the same Strata-data/memory-eval store, recall_limit 16, max_block_chars 12000),
                sent as the conversation's system message with X-Strata-Memory: off so the
                server adds nothing. Guard: rendered ids == live usage.prompt_tokens, exact.

Every ask: temperature per --temp (default 0.8, the user's regime), reasoning_effort "off",
max_tokens 200 - like gate1_driver.chat. A run whose live prompt size differs from the locally
rendered one is VOID, not scored (the store prepending ~310 unmodelled tokens burned the first
Venue-A pass; the guard exists to make that impossible to miss).

Answers are scored with gate1_driver.hit() against eval_questions.csv golds; the recall RATE
counts only questions whose COLD verdict (eval_candidates.tsv) was not "known" - the same
targets definition state/gate1_score.json used, so 10/100 and any new number are comparable.
Injected-token cost is reported next to every recall rate (plan T1: "beat 10/100 and report
injected tokens next to it").

Usage:
  .venv/bin/python spikes/window/gate1_rerun.py --arm local16 --passes 3 [--temp 0.8] [--dry-run]
Evidence: state/gate1_rerun_<arm>.jsonl (every ask) + state/gate1_rerun_verdict_<arm>.txt.
"""
import argparse, json, os, sys, time, urllib.request as U
from pathlib import Path

HERE = Path(__file__).resolve().parent          # spikes/window
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "spikes" / "d2_dissect"))
sys.path.insert(0, str(HERE))

from gate1_driver import cold_map, hit, norm, rows               # noqa: E402
from venue_render import load_template, load_tokenizer, live_render  # noqa: E402
from serve.memory import MemoryService                              # noqa: E402

STORE = ROOT / "Strata-data" / "memory-eval"
ARMS = {"live8": dict(limit=8, budget=6000, muted=False),
        "local8": dict(limit=8, budget=6000, muted=True),
        "local16": dict(limit=16, budget=12000, muted=True)}


def ask(url, messages, temp, muted):
    body = {"messages": messages, "temperature": temp, "reasoning_effort": "off", "max_tokens": 200}
    req = U.Request(url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                    method="POST", headers={"Content-Type": "application/json"})
    if muted:
        req.add_header("X-Strata-Memory", "off")
    t0 = time.time()
    with U.urlopen(req, timeout=600) as r:
        j = json.loads(r.read())
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    return (msg.get("content") or ""), time.time() - t0, (j.get("usage") or {}).get("prompt_tokens")


def build_block(mem, question, query=None):
    """Exactly what request_state+block_for would inject for a fresh one-message conversation:
    the pinned head block built from this question (limit/budget = mem's), riding as the system
    message because the client sent no leading system. query= overrides the recall text (the
    --query word mode: Gate-1 verify()'s distinctive word, norm(question).split()[-3]) - the
    SENT question never changes, only what the block is selected with."""
    block, hits, _q = mem.block_for([{"role": "user", "content": query or question}], "default")
    if block is None:
        return None, []
    return block, [h["id"] for h in hits]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arm", choices=sorted(ARMS))
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--passes", type=int, default=1)
    ap.add_argument("--temp", type=float, default=0.8)
    ap.add_argument("--query", choices=["question", "word"], default="question",
                    help="what the block is SELECTED with: the full question (default) or "
                         "Gate-1 verify()'s single distinctive word (degraded-query probe: does a "
                         "16-wide window recall what an 8-wide one drops when the query shares few "
                         "words with the facts?)")
    ap.add_argument("--dry-run", action="store_true", help="render 3 asks, contact nothing")
    a = ap.parse_args()
    cfg = ARMS[a.arm]
    R = rows()
    if a.dry_run:
        R = R[:3]
    gold_id = {}
    try:                                        # teach's n->fact id map: was the gold even IN the block?
        for e in json.loads((STATE / "gate1_ids.json").read_text()):
            gold_id[e["n"]] = e["id"]
    except (OSError, ValueError, KeyError):
        pass
    cold = cold_map()
    targets = [i for i, r in enumerate(R, 1) if cold.get(r["question"], {}).get("verdict") != "known"]
    tok, tpl = load_tokenizer(), load_template()
    mem = MemoryService(str(STORE), recall_limit=cfg["limit"], max_block_chars=cfg["budget"])

    recs, geo_bad = [], 0
    t_start = time.time()
    for p in range(1, a.passes + 1):
        for i, r in enumerate(R, 1):
            q = r["question"]
            sel = norm(q).split()[-3] if a.query == "word" else None
            if a.query == "word" and not sel:
                sel = norm(q).split()[0]
            block, shown = build_block(mem, q, sel)
            armed = ([{"role": "system", "content": block}] if block else []) + [{"role": "user", "content": q}]
            armed_ids, _ = live_render(tok, tpl, armed, "off")
            bare_ids, _ = live_render(tok, tpl, [{"role": "user", "content": q}], "off")
            inj = len(armed_ids) - len(bare_ids)
            sent = armed if cfg["muted"] else [{"role": "user", "content": q}]
            if a.dry_run and p == 1 and i <= 3:
                print(f"[dry {i}] armed={len(armed_ids)} bare={len(bare_ids)} injected={inj} "
                      f"facts={len(shown)} | {q[:70]}")
                continue
            # live8 sends the bare question with NO header -> the server injects its own block;
            # the local arms carry our rendered block and mute the server with X-Strata-Memory: off.
            ans, secs, ptok = ask(a.url, sent, a.temp, cfg["muted"])
            ok = ptok == len(armed_ids)
            if not ok:
                geo_bad += 1
            inblk = (i in gold_id and gold_id[i] in shown) if gold_id else None
            recs.append({"pass": p, "n": i, "kind": cold.get(q, {}).get("kind", "?"), "ids": shown,
                         "gold_in_block": inblk,
                         "armed": len(armed_ids), "bare": len(bare_ids), "injected": inj,
                         "ptok": ptok, "expected": len(armed_ids), "geometry_ok": ok,
                         "secs": round(secs, 2), "answer": ans, "correct": bool(hit(ans, r["expected_answer_contains"]))})
        if not a.dry_run:
            print(f"pass {p} done in {time.time() - t_start:.0f}s ({geo_bad} geometry mismatches so far)", flush=True)
    if a.dry_run:
        mem.close()
        return
    STATE.mkdir(exist_ok=True)
    tag = a.arm + ("_w1q" if a.query == "word" else "")
    (STATE / f"gate1_rerun_{tag}.jsonl").write_text("\n".join(json.dumps(x) for x in recs) + "\n")

    # per-pass scoring, same targets definition as gate1_driver.score
    lines = [f"GATE-1 RERUN arm={a.arm} query={a.query} temp={a.temp} passes={a.passes} "
             f"store={mem.db_path.replace(os.path.expanduser('~'), '$HOME')} "
             f"({mem.stats()['memories']} facts)",
             f"geometry: {len(recs) - geo_bad}/{len(recs)} asks matched the locally rendered prompt size"
             + (" -> run VALID" if geo_bad == 0 else " -> VOID (the guard does not score around it)"),
             f"cold-target set: {len(targets)}/{len(R)} questions (verdict != known in eval_candidates.tsv)"]
    inj_all = [x["injected"] for x in recs]
    lines.append(f"injected tokens per ask: avg {sum(inj_all) / len(inj_all):.0f} "
                 f"min {min(inj_all)} max {max(inj_all)}")
    for p in range(1, a.passes + 1):
        pr = [x for x in recs if x["pass"] == p]
        rec = sum(1 for x in pr if x["n"] in targets and x["correct"])
        misses = [x["n"] for x in pr if x["n"] in targets and not x["correct"]]
        if a.query == "word":                   # separate the two ways an arm can fail
            inb = sum(1 for x in pr if x["n"] in targets and x["gold_in_block"])
            miss_inb = [x["n"] for x in pr if x["n"] in targets and not x["gold_in_block"]]
            lines.append(f"  pass {p}: gold fact IN the block {inb}/{len(targets)}; "
                         f"recall {rec}/{len(targets)}; out-of-block misses: {len(miss_inb)} {miss_inb[:15]}")
        else:
            lines.append(f"  pass {p}: recall {rec}/{len(targets)}" + (f"  misses: {misses}" if len(misses) <= 30 else
                         f"  ({len(misses)} misses, see jsonl)"))
    best = max(sum(1 for x in recs if x["pass"] == p and x["n"] in targets and x["correct"])
               for p in range(1, a.passes + 1))
    lines.append(f"verdict vs pre-registered GATE-1 bar (>= 25/{len(targets)}): "
                 f"{'PASS' if best * 100 // len(targets) >= 25 else 'NOT MET'} (best pass {best}/{len(targets)}; "
                 f"the 2026-10-11 morning run scored 10/100)")
    verdict = "\n".join(lines)
    (STATE / f"gate1_rerun_verdict_{tag}.txt").write_text(verdict + "\n")
    print(verdict)
    mem.close()


STATE = HERE / "state"
if __name__ == "__main__":
    main()
