#!/usr/bin/env python3
"""spikes/cartridge/c1_plumb.py - C1: re-prove the session save/restore plumbing on THIS build.

Spike A's numbers (104 tok / 237 MB / 155.7 ms byte-exact across a restart) were taken on
0.1.42 + yarn-2; the file header pins (engine version, resolved rope, --max-context, model
fingerprint - see C0_save_restore_code_map.md) make 0.1.43 + 393216/yarn-1.5 a different file
class, so the plumbing must be re-established before any C2 ask is trusted.

Checks, in order (a failure blocks C2):
  1. the endpoint is live (501 = the boot lacks the slot_save_path SERVER config key -> print
     the fix, exit 2);
  2. tiny encode conversation (one user message with a nonce codeword, short ack), save -> file
     appears in the slot dir, n_saved/n_written/save_ms recorded;
  3. restore -> n_restored == n_saved, restore_ms recorded (the attach cost on this build);
  4. save again IMMEDIATELY (no ask in between) -> byte-compare the two files: identical bytes =
     round-trip integrity; if they differ, report the first differing offset + the header span
     (honesty: the format's header content on re-save is not specced to be timestamp-free);
  5. restore, then a continuation ask THROUGH THE SERVER ("what codeword?") -> the nonce must
     come back (the restored state is really the model's live conversation) AND
     timings.cache_n >= n_saved - slack (the ride is counted by the SERVER's bookkeeping, the
     untested territory C0 flagged) AND usage.prompt_tokens == the local venue_render render.

Channel: reasoning_effort "off", temperature 0.8 (protocol: never 0 on this build), every request
memory-muted (X-Strata-Memory: off). Writes state/c1_plumb.txt; refuses to run against a venue
whose geometry it cannot render (offline render failure = exit 3).

Usage: python3 spikes/cartridge/c1_plumb.py [--url http://127.0.0.1:8005]
         [--slot-dir Strata-data/sessions-dev] [--filename c2_plumb_probe] [--slack 8]
"""
from __future__ import annotations
import argparse, datetime, hashlib, json, re, sys, time, urllib.error, urllib.request as U
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "spikes" / "d2_dissect"))
from venue_render import load_tokenizer, load_template, live_render  # noqa: E402
from strata_control.datafolder import data_dir                       # noqa: E402  single-root law (2026-10-23)


def http(url, payload=None, headers=None, timeout=960):
    """-> (status, parsed_body); HTTPError bodies are parsed, not raised. Transport errors
    (socket.timeout included) come back as (0, {"raw": ...}): the worst-case IN-request engine
    reload is ENGINE_READY_S=900 (server.py:265) and idle_unload_s=700 makes it reachable, so
    960 s covers it and a transport failure is RECORDED, never a lost-evidence traceback."""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--slot-dir", default=str(data_dir(ROOT) / "sessions-dev"),
                    help="the directory the boot's --slot-save-path points at (for byte-hashing "
                         "the files the server writes)")
    ap.add_argument("--filename", default="c2_plumb_probe")
    ap.add_argument("--slack", type=int, default=8)
    a = ap.parse_args()
    url = a.url.rstrip("/")
    lines, fails = [], []

    def say(s):
        print(s)
        lines.append(s)

    say("c1 plumb @ " + datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        + f" url={url} channel=reasoning_effort=off,temperature=0.8,memory-muted")

    st, health = http(url + "/health")
    model = health.get("model") or "strata"
    say(f"health: {health.get('status')} model={model} max_context={health.get('max_context')}")

    nonce = format(int(time.time()) % 100000, "05d")
    try:  # the docstring's promise: an offline render failure is exit 3, not a traceback
        tok = load_tokenizer()
        tpl = load_template()
    except Exception as e:
        print(f"offline render venue unavailable: {e} - exit 3, no ask sent")
        return 3

    # 2. tiny encode conversation + save
    conv = [{"role": "user", "content": f"Remember exactly this codeword: zephyr-quill-{nonce}. "
                                        "Reply with OK and nothing else."}]
    body = {"model": model, "messages": conv, "temperature": 0.8, "reasoning_effort": "off",
            "max_tokens": 8, "stream": False}
    st, r = http(url + "/v1/chat/completions", body, headers={"X-Strata-Memory": "off"})
    ack = ((r.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    conv.append({"role": "assistant", "content": ack.strip()})
    say(f"encode: ack={ack.strip()!r} ptok={(r.get('usage') or {}).get('prompt_tokens')}")

    st, sv = http(url + "/slots/0?action=save", {"filename": a.filename})
    if st == 501:
        say("SAVE -> 501: " + str((sv.get("error") or {}).get("message") or json.dumps(sv)[:160]))
        say("FIX: set the SERVER config key  \"slot_save_path\": \"Strata-data/sessions-dev\"  in "
            "strata-dev.json (NOT an engine arg - the engine rejects unknown flags and dies "
            "before READY, audit 2026-10-11) and restart the dev boot (./dev-down.sh && "
            "./dev-up.sh --force). C2 is blocked until C1 passes.")
        (HERE / "state").mkdir(parents=True, exist_ok=True)
        (HERE / "state/c1_plumb.txt").write_text("\n".join(lines) + "\n")
        return 2
    if st != 200:
        say(f"SAVE -> {st}: {json.dumps(sv)[:200]}  FAIL - C2 blocked")
        (HERE / "state").mkdir(parents=True, exist_ok=True)
        (HERE / "state/c1_plumb.txt").write_text("\n".join(lines) + "\n")
        return 1
    n_saved, save_ms = sv["n_saved"], sv["timings"]["save_ms"]
    path = Path(a.slot_dir) / a.filename
    h1 = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    say(f"save: n_saved={n_saved} n_written={sv['n_written']} B save_ms={save_ms} "
        f"file={'present' if path.exists() else 'MISSING'} sha256={str(h1)[:16]}")
    if h1 is None:
        fails.append("saved file not found in --slot-dir")

    # 3. restore
    st, rs = http(url + "/slots/0?action=restore", {"filename": a.filename})
    say(f"restore: status={st} n_restored={rs.get('n_restored')} n_read={rs.get('n_read')} "
        f"restore_ms={(rs.get('timings') or {}).get('restore_ms')}")
    if st != 200 or rs.get("n_restored") != n_saved:
        fails.append(f"restore status/{rs.get('n_restored')} vs n_saved {n_saved}")

    # 4. immediate re-save, byte-compare (round-trip integrity). The status is CHECKED (audit
    # 2026-10-11): a failed re-save used to skip this headline check silently - h2 None, no fail
    # recorded - and C1 could print PASS with the integrity check never executed.
    st, sv2 = http(url + "/slots/0?action=save", {"filename": a.filename + "_rt"})
    if st != 200:
        fails.append(f"re-save -> {st}: {json.dumps(sv2)[:120]} - round-trip integrity NOT proven")
    rt = Path(a.slot_dir) / (a.filename + "_rt")
    h2 = hashlib.sha256(rt.read_bytes()).hexdigest() if st == 200 and rt.exists() else None
    if st == 200 and h2 is None:
        fails.append(f"re-save 200 but {rt} not found in --slot-dir - integrity NOT proven")
    if h1 and h2:
        if h1 == h2:
            say(f"re-save byte-exact: sha256 match ({h2[:16]}) - round-trip integrity PASS")
        else:
            b1, b2 = path.read_bytes(), rt.read_bytes()
            off = next((i for i in range(min(len(b1), len(b2))) if b1[i] != b2[i]), min(len(b1), len(b2)))
            say(f"re-save DIFFERS at byte {off} (len {len(b1)} vs {len(b2)}): header re-save "
                f"content is not specced timestamp-free - recorded honestly, first diff "
                f"{b1[off:off+16]!r} vs {b2[off:off+16]!r}")
            fails.append("re-save not byte-exact (see line above)")

    # 5. restore + continuation ask THROUGH THE SERVER (the ride + the recall of the nonce).
    # The status is CHECKED (audit 2026-10-11): if THIS restore failed, the ask below may ride
    # RESIDUAL live state (step 4's re-save left the bank live) - everything could print green
    # while the restore->ask adjacency that C2b's whole shape depends on stays unproven.
    st, rs = http(url + "/slots/0?action=restore", {"filename": a.filename})
    if st != 200:
        fails.append(f"restore #2 -> {st}: {json.dumps(rs)[:120]} - the ask below may ride "
                     "residual live, not the restore (the C2b shape is exactly what it tests)")
    ask = conv + [{"role": "user", "content": "And the answer: what codeword were you asked to "
                                              "remember? Answer with just the codeword."}]
    try:
        ids, _ = live_render(tok, tpl, ask, "off")
    except Exception as e:
        say(f"render of the continuation ask failed: {e} - exit 3, no ask sent")
        (HERE / "state").mkdir(parents=True, exist_ok=True)
        (HERE / "state/c1_plumb.txt").write_text("\n".join(lines) + "\n")
        return 3
    body = {"model": model, "messages": ask, "temperature": 0.8, "reasoning_effort": "off",
            "max_tokens": 24, "stream": False}
    st, r = http(url + "/v1/chat/completions", body, headers={"X-Strata-Memory": "off"})
    ans = (((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    ptok = (r.get("usage") or {}).get("prompt_tokens")
    t = r.get("timings") or {}
    cache_n, prompt_n = t.get("cache_n"), t.get("prompt_n")
    geom_ok = ptok == len(ids)
    ride_ok = cache_n is not None and cache_n >= n_saved - a.slack
    recall_ok = bool(re.search(r"zephyr-quill-" + nonce, ans.lower()))
    say(f"continuation ask: ptok={ptok}/{len(ids)} geometry {'OK' if geom_ok else 'MISMATCH'} | "
        f"cache_n={cache_n} prompt_n={prompt_n} (need cache_n>={n_saved - a.slack}) ride "
        f"{'OK' if ride_ok else 'NO-GO'} | answer {ans[:40]!r} -> codeword recall "
        f"{'FOUND' if recall_ok else 'MISSED'}")
    if not geom_ok:
        fails.append("ask geometry mismatch")
    if not ride_ok:
        fails.append("ride guard: server did not count the restored prefix as reused")
    if not recall_ok:
        fails.append("restored conversation did not answer from its own state")

    verdict = "C1 PASS - the venue is open on this build; C2 may run" if not fails else \
              "C1 FAIL - " + "; ".join(fails)
    say(verdict)
    (HERE / "state").mkdir(parents=True, exist_ok=True)
    (HERE / "state/c1_plumb.txt").write_text("\n".join(lines) + "\n")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
