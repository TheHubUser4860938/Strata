#!/usr/bin/env python3
"""lme_retrieval_ab - does the reranker help retrieval, measured WITHOUT generating an answer?

The accuracy venue answers 500 questions x 3 repeats: every line pays a prefill (a 12k-token first
turn is ~8.5 s at the measured 1,400 tok/s) plus a judge call, so one arm is ~1.5-2 h of owned port.
But the retrieval metrics - top-1/top-3 and the multi-evidence coverage fields - need only the store's
own search: 500 reads per arm, no engine, no judge, ~2 minutes. Same questions, same scopes, same gold
map, and the same scoring function the accuracy run uses (lme_harness.recall_metrics), so a mechanism
number and an outcome number can never disagree about what one hit list meant.

That makes this the MECHANISM measurement and the accuracy venue the OUTCOME measurement. If the
reranker cannot beat the lexical store on its own ground here, "the embedder is always a positive" is
in trouble before a single token is generated; if it wins everywhere here and the accuracy run comes
back flat, the flatness is a budget/attention story, not a retrieval one - and those two readings want
opposite shipping decisions.

Two ways to run it, because the second one needs no server at all:

  # against a live server, arms split by the per-request reranker mute (one process, one store)
  python3 tools/lme_retrieval_ab.py --data longmemeval_oracle.json --map <shared.map> \
      [--url http://127.0.0.1:8005] [--recall-k 10] [--wide-k 0] [--limit N] [--types ...]

  # against a COPY of any store, sealed or not, in-process: no boot, no engine, nothing on the port
  python3 tools/lme_retrieval_ab.py --data ... --map ... --offline Strata-data/memory-venue

Offline opens a copy with encrypt=True, so a ciphertext-only venue store reveals itself to this
process (the key sits beside the copy), backfills the vectors the hybrid arm needs if they are
missing, and then answers every question twice - once from a service with the reranker, once without.
A sealed store has no plaintext to disturb and the running server is never touched.

Two checks run before any number is believed, because an A/B whose lever never moved reports a
difference that is not there:
  * every arm must be the arm it claims - over HTTP that is the reply's own "embedder" field, offline
    it is the service's actual embedder attribute, read back rather than assumed;
  * the two arms must return different hit lists for some questions. Identical lists everywhere means
    the reranker is not loaded, not configured, or the store has no vectors: exit 2, not a null
    comparison reported as a result.

`--wide-k` scores a second pass at the block's own note budget (e.g. 192), which is where the
coverage question actually lives: a three-event ordering question cannot be answered from a top-10
list no matter how good the ranking is.
"""
from __future__ import annotations

import argparse, json, shutil, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the offline arm opens serve.memory
import lme_harness as H                                          # noqa: E402

ARMS = (("lexical", True), ("hybrid", False))                    # (label, no_embedder)
METRICS = (("turn_top1", "turn_top1"), ("turn_top3", "turn_top3"),
           ("row_cov", "row_coverage"), ("sess_cov", "session_coverage"))


def scored(q: dict, where: dict, ids: list, arm, k: int) -> dict:
    """One question, one hit list, one verdict - through the venue's own scorer."""
    return {"question_id": q["question_id"], "type": q.get("question_type", "?"), "ids": ids,
            "arm": arm, **H.recall_metrics(ids, where, set(q.get("answer_session_ids") or []),
                                           q["question_id"].endswith("_abs"), k)}


def sweep_http(url: str, data: list, maps: dict, k: int, no_embedder: bool) -> list[dict]:
    rows = []
    for q in data:
        where = maps.get(q["question_id"])
        if where is None:
            continue                                   # not ingested by this store: nothing to score
        r = H.recall(url, q, f"lme_{q['question_id']}", where, k, no_embedder=no_embedder)
        rows.append({"question_id": q["question_id"], "type": q.get("question_type", "?"), **r})
    return rows


def sweep_offline(svc, data: list, maps: dict, k: int) -> list[dict]:
    """Same work, no HTTP: the store's own search() answers, exactly as the server's GET does."""
    rows = []
    for q in data:
        where = maps.get(q["question_id"])
        if where is None:
            continue
        hits = svc.search(q["question"], scope=f"lme_{q['question_id']}", limit=k)
        ids = [h["id"] for h in hits]
        r = scored(q, where, ids, svc.embedder is not None, k)
        # provenance rides the DUMP, not the metrics table: "the reranker moved rank 1" is a number, but
        # the -0.139 post-mortem needed to know WHICH ARM landed the winner and where the gold's own row
        # sat in each list - that was reconstructed by hand here, and it is now a field.
        r["prov"] = [[h["id"], h.get("lex_rank"), h.get("cos_rank")] for h in hits]
        rows.append(r)
    return rows


def agg(rows: list[dict]) -> dict:
    out = {}
    for key in ("turn_top1", "turn_top3", "session_top1", "session_top3",
                "row_coverage", "session_coverage"):
        vals = [r[key] for r in rows if r.get(key) is not None]
        out[key] = round(sum(vals) / len(vals), 3) if vals else None
    return out


def copy_dir(src: Path, dst: Path) -> None:
    """Honest PG-native clone (R5b): a consistent pg_dump/pg_restore through tools/_storeclone,
    which works while the source store is live. The old whole-dir file loop was a sqlite-era
    lie: on a Postgres store it copies the frozen legacy memory.db.enc and skips pgdata/
    entirely, so the copy arrives stale-or-empty, never live. The key travels with the clone."""
    import _storeclone
    _storeclone.clone_store(src, dst)


def open_copy(dir_path: Path, model: str, threads: int, embed: bool, dedup: bool = True):
    """`dedup=False` turns off the READ-PATH near-dupe collapse only (serve.memory dedup_recall),
    the ranking untouched. It exists for the knowledge-update question raised twice from outside
    on 2026-10-15: Primnox measured that retiring old facts HURTS on real two-person chats (plain
    search 45.7 top-1 vs their 40.0-42.8) because most such questions are ABOUT the change, and a
    Strata A/B had already measured -0.139 turn_top1 on the same question type. A collapse that
    keeps one of two near-identical rows is indistinguishable from a retirement when the question
    needs both, so the mechanism has to be separated from the ranking before any demotion rule
    gets built."""
    from serve.memory import MemoryService                        # late: the HTTP arm needs neither
    kw = {}
    if embed:
        from serve.embedder import Embedder
        kw["embedder"] = Embedder(model, threads=threads)
    return MemoryService(str(dir_path), recall_limit=192, max_block_chars=48000, encrypt=True,
                         dedup_recall=dedup, key_file=str(dir_path / "memory.key"), **kw)


def prepare_offline(store_dir: str, model: str, threads: int, work: Path) -> tuple[Path, Path]:
    """Reveal a copy, embed what the hybrid arm needs, then split into two copies: one service will
    run WITH the reranker and one without, over identical rows. Embedding happens once, before the
    split, so neither arm is measured against a store that is half-vectorised.

    `--work` names the directory so an analysis loop does not re-embed 11k rows every pass: the
    embedded copy is reused when it is already there (the copy step is a no-op overwrite and the
    plaintext db outranks the stale .enc - the husk rule tells them apart)."""
    src = Path(store_dir).resolve()
    if not (src / "memory.db").exists() and not (src / "memory.db.enc").exists():
        print(f"no store in {src}", file=sys.stderr)
        raise SystemExit(2)
    hyb = work / "hyb"
    copy_dir(src, hyb)
    t0 = time.time()
    m = open_copy(hyb, model, threads, True)               # this open reveals a sealed store
    n = m.stats().get("memories", 0)
    print(f"[ab] copy opened: {n} active rows ({time.time() - t0:.0f} s)", flush=True)
    with m.lock:
        missing = m.conn.execute("select id, text from memories where invalid_at is null and id not in"
                                 " (select id from embeddings) order by id").fetchall()
    if missing:
        print(f"[ab] embedding {len(missing)} rows for the hybrid arm, once, up front ...", flush=True)
        emb = m.embedder
        with m.lock:
            for i in range(0, len(missing), 64):
                chunk = missing[i:i + 64]
                vecs = emb.embed_docs([r[1] for r in chunk])
                m.conn.executemany("insert or replace into embeddings values(?,?)",
                                   [(r[0], v.tobytes()) for r, v in zip(chunk, vecs)])
            m.conn.commit()
    with m.lock:
        left = m.conn.execute("select count(*) from memories where invalid_at is null and id not in"
                              " (select id from embeddings)").fetchone()[0]
    m.close()
    if left:
        print(f"[ab] {left} active rows still have no vector - the hybrid arm would be scored against "
              "a half-embedded store", file=sys.stderr)
        raise SystemExit(2)
    lex = work / "lex"
    copy_dir(hyb, lex)
    print(f"[ab] embedded and split into two copies: every vector present, both arms see the same rows",
          flush=True)
    return lex, hyb


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True)
    ap.add_argument("--map", dest="map_path", required=True, help="the gold sidecar the ingest wrote")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--offline", default="", help="run against a COPY of this store dir, in-process")
    ap.add_argument("--work", default="", help="keep the embedded copy here and reuse it next pass "
                                               "(saves re-embedding ~11k rows on every analysis run)")
    ap.add_argument("--dump", default="", help="write one JSONL line per question per arm: the hit "
                                               "list plus its metrics, so a per-type regression can be "
                                               "read question by question instead of re-running")
    ap.add_argument("--model", default="Strata-data/embeddings/bge-small-en-v1.5")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--recall-k", type=int, default=10, help="slots to score (the accuracy run's k)")
    ap.add_argument("--wide-k", type=int, default=0, help="optional second pass at the block budget")
    ap.add_argument("--rrf-k", type=float, default=None,
                    help="override serve.memory.RRF_K for this run. The shipped value is 60 (the RRF "
                         "paper's convention); Graphiti and Zep both ship rank_const=1 for the same "
                         "fusion, so this flag exists to settle it on OUR store instead of on "
                         "convention. Deterministic: one run is one answer, no sampling.")
    ap.add_argument("--no-dedup", dest="no_dedup", action="store_true",
                    help="offline only: disable the read-path near-dupe collapse in the scoring arms")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--types", default="")
    a = ap.parse_args(argv)

    if a.rrf_k is not None:           # the fusion constant is a module global precisely so a probe can
        import serve.memory as _m     # move it without touching the shipped default (serve.memory.RRF_K)
        _m.RRF_K = float(a.rrf_k)
        print(f"[ab] RRF_K overridden to {_m.RRF_K} (shipped default 60)")

    data = json.load(open(a.data))
    types = {t.strip() for t in a.types.split(",") if t.strip()}
    if types:
        data = [q for q in data if q.get("question_type") in types]
    if a.limit:
        data = data[:a.limit]
    maps = H.load_map(a.map_path)
    if not maps:
        print(f"no gold map at {a.map_path} - the A/B cannot score anything", file=sys.stderr)
        return 2
    print(f"[ab] {len(data)} questions x {len(ARMS)} arms, k={a.recall_k}, map holds {len(maps)} "
          f"questions; no engine calls, so this is store time only", flush=True)

    tmp, services = None, {}
    try:
        if a.offline:
            tmp = Path(a.work).resolve() if a.work else Path(tempfile.mkdtemp(prefix="lmeab-"))
            lex, hyb = prepare_offline(a.offline, a.model, a.threads, tmp)
            nd = not a.no_dedup
            if not nd:
                print("[ab] read-path near-dupe collapse OFF in the scoring arms (write-path laws unchanged)")
            services = {"lexical": open_copy(lex, a.model, a.threads, False, dedup=nd),
                        "hybrid": open_copy(hyb, a.model, a.threads, True, dedup=nd)}
            for s in services.values():
                s.search("warm", limit=1)             # build the caches before any timing claim
        for ki, k in enumerate([a.recall_k] + ([a.wide_k] if a.wide_k else [])):
            res, mismatches, t0 = {}, 0, time.time()
            for label, mute in ARMS:
                if a.offline:
                    rows = sweep_offline(services[label], data, maps, k)
                else:
                    rows = sweep_http(a.url, data, maps, k, mute)
                want_arm = (label != "lexical")      # the reply's echo: True only for the hybrid arm
                mismatches += sum(1 for r in rows if r.get("arm") is not None
                                  and bool(r["arm"]) != want_arm)
                res[label] = rows
            if len(res.get("lexical", [])) != len(res.get("hybrid", [])):
                print("[ab] the arms scored different question sets - the store changed mid-pass",
                      file=sys.stderr)
                return 2
            if mismatches:
                print(f"[ab] {mismatches} replies reported an arm that does not match the request sent"
                      " - the header is not being honoured; these numbers are NOT an A/B",
                      file=sys.stderr)
                return 2
            by_q = {r["question_id"]: r for r in res["hybrid"]}
            same = sum(1 for r in res["lexical"]
                       if by_q[r["question_id"]]["ids"][:k] == r["ids"][:k])
            print(f"\n=== k={k} ===  {time.time() - t0:.0f} s for {len(res['lexical']) * 2} recalls",
                  flush=True)
            if same == len(res["lexical"]):
                print("[ab] both arms returned identical hit lists for EVERY question: the reranker "
                      "did not move. Check that memory.embedder is configured and the store is "
                      "embedded (stats -> embedder.vectors). Exiting 2 rather than reporting a null "
                      "comparison as a result.", file=sys.stderr)
                return 2
            print(f"  identical hit lists: {same}/{len(res['lexical'])} "
                  f"(the lever moved on {len(res['lexical']) - same})")
            if a.dump:                                  # the per-question record of BOTH arms, so a
                with open(a.dump, "w" if ki == 0 else "a") as fh:   # regression can be read case by
                    for lab in ("lexical", "hybrid"):   # case. "a" after the first k pass, so the
                        for r in res[lab]:              # wide pass adds lines instead of erasing them
                            fh.write(json.dumps({"k": k, "label": lab, **r}) + "\n")
                            # "label" is what the run asked for; r["arm"] is what the reply said -
                            # naming both keeps a mis-set header visible in the record itself
                    print(f"  dumped {2 * len(res['lexical'])} lines to {a.dump} (k={k})")
            print("  " + f"{'type':<26}{'n':>4}" + "".join(f"{lbl:>18}" for lbl, _ in METRICS))
            for scope in ("OVERALL",) + tuple(sorted({r["type"] for r in res["lexical"]})):
                sel = res
                if scope != "OVERALL":
                    sel = {lab: [r for r in v if r["type"] == scope] for lab, v in res.items()}
                n = len(sel["lexical"])
                if not n:
                    continue
                A, B = agg(sel["lexical"]), agg(sel["hybrid"])
                cells = ""
                for _lbl, key in METRICS:
                    if A[key] is None or B[key] is None:
                        cells += f"{'n/a':>18}"
                    else:
                        cells += f"{A[key]:.3f}->{B[key]:.3f} {B[key] - A[key]:+.3f}".rjust(18)
                print(f"  {scope:<26}{n:>4}" + cells)
        print("\n[ab] read as: the mechanism measurement. The accuracy venue is the outcome; this "
              "only says whether the reranker moved the evidence, per type, on the store's own search.")
        return 0
    finally:
        for s in services.values():
            try:
                s.close()
            except Exception:
                pass
        if tmp is not None and not a.work:      # --work keeps the embedded copy for the next pass
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
