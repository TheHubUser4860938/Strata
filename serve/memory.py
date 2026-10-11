"""serve/memory.py — the Memory Core: built-in, harness-independent recall (memory-core fork).

The server keeps a persistent memory of what it has been told through any client, and answers future
clients from it without the harness ever resending memory text: retrieval happens here, the recalled
block is merged into the head of the conversation's leading system turn (the Qwen template allows one
system message only, at index 0 - with no system sent, the block rides as one), and every completed
turn is journaled for curation. Design, measured constraints and the cartridge research track:
docs/MEMORY_CORE.md.

Off unless the engine config carries a "memory" block or the server runs with --memory DIR; with it
absent every code path stays byte-identical to upstream Strata. Storage is one SQLite file (WAL) with
an FTS5 index - stdlib only, no new dependencies. An optional local embedder can join the ranking
later; nothing here requires it.

Block stickiness is deliberate, and so is its half-life. The block a conversation is SHOWN at the head
is computed once - keyed by scope and the conversation's first user message - and pinned in an LRU
cache, so the rendered prompt keeps its leading bytes stable while Strata's prompt checkpoints reuse
them, even as the store changes underneath. A new conversation or an evicted key picks up the store's
current version. Pinning alone, though, froze recall at the opening question: measured at Gate 1
(2026-10-11, docs/MEMORY_CORE.md "Phase 2") an 8-fact block pinned to the first user message answered
one question per conversation and missed the rest. So per-turn recall rides the TAIL instead: each
request re-searches with the conversation's NEWEST user message and merges what is new (facts the head
block does not already carry) into that message - a handful of bytes at the end of the prompt, where
the bytes change every turn anyway. Head stable, tail fresh: prefix reuse survives and recall follows
the conversation. One switch, "per_turn": false, restores the frozen-head behaviour.

Ideas of numerical memory behind this service are inspired by AKBASCORE MAM / NIRVANA
(https://github.com/ceceli33/titan-cognitive-core-v2, © 2026 Mustafa Akbaş); this implementation is
independent and keeps Strata's MIT license.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

SCOPE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}\Z")
USER_SCOPE = "user"                     # the profile shared by every client and project
DEFAULT_PROJECT = "default"             # requests that name no scope land in this project bucket
WORD_RE = re.compile(r"\w{2,}", re.UNICODE)
MAX_QUERY_WORDS = 12                    # a MATCH over more words than these stops being a query and becomes noise
AGE_SCALE_DAYS = 90.0                   # recency half-life-ish: exp(-age_days / this) feeds the ranking blend


def scope_name(value, fallback: str = DEFAULT_PROJECT) -> str:
    """A scope as stored: kept when it looks like a plain name, else the fallback. Never raises - a request
    header may carry anything."""
    v = (value or "").strip()
    return v if SCOPE_RE.fullmatch(v) else fallback


def norm_text(text: str) -> str:
    """Casefolded, whitespace-collapsed: dedup identity and cache keys."""
    return " ".join("".join([c if c.isalnum() or c.isspace() else " " for c in str(text).casefold()]).split())


def text_of(content) -> str:
    """A message's text from either shape: a plain string, or OpenAI's list of content parts (images are
    skipped - retrieval is over text)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict) and part.get("type") in (None, "text") and isinstance(part.get("text"), str):
                out.append(part["text"])
        return "\n".join(out)
    return str(content)


def memory_dir(value: str, base: str | None = None) -> str:
    """The store directory as one absolute, normalized path (relative to the config's "cwd" when it has
    one), created private (mode 0700) when missing.  ValueError for anything unusable."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('memory "dir" must be a directory path (a string)')
    if any(ord(c) < 32 for c in value):
        raise ValueError('memory "dir" must not contain control characters')
    path = os.path.abspath(os.path.join(base, value) if base and not os.path.isabs(value) else value)
    os.makedirs(path, mode=0o700, exist_ok=True)
    if not os.path.isdir(path):
        raise ValueError(f"memory dir {path} is not a directory")
    return path


class MemoryService:
    """The store, the recall and the journal. Thread-safe: every statement runs under one lock on a single
    connection (WAL, busy_timeout) - the server's request threads write from several at once."""

    def __init__(self, directory: str, *, max_block_chars: int = 6000, recall_limit: int = 8,
                 cache_entries: int = 64, inject: bool = True, per_turn: bool = True,
                 turn_recall_limit: int = 8, turn_block_chars: int = 2000):
        self.directory = memory_dir(directory)      # absolute, created 0700 - same rules as slot_save_path
        self.db_path = os.path.join(self.directory, "memory.db")
        self.max_block_chars = max(200, int(max_block_chars))
        self.recall_limit = max(1, min(50, int(recall_limit)))
        self.per_turn = bool(per_turn)
        self.turn_recall_limit = max(1, min(50, int(turn_recall_limit)))
        self.turn_block_chars = max(200, int(turn_block_chars))
        self.inject = bool(inject)
        self.lock = threading.Lock()
        self._blocks: OrderedDict[tuple[str, str], tuple[str, list[int]]] = OrderedDict()  # (scope, key) -> pinned (block, shown ids)
        self._cache_entries = max(1, int(cache_entries))
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  scope TEXT NOT NULL,
                  text TEXT NOT NULL,
                  source TEXT NOT NULL DEFAULT 'manual',
                  provenance TEXT,
                  confidence REAL NOT NULL DEFAULT 0.8,
                  created_at REAL NOT NULL,
                  updated_at REAL NOT NULL,
                  last_seen_at REAL,
                  hits INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS turns(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  scope TEXT NOT NULL,
                  user_text TEXT NOT NULL,
                  assistant_text TEXT NOT NULL,
                  model TEXT,
                  created_at REAL NOT NULL,
                  processed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
                  text, content='memories', content_rowid='id',
                  tokenize='porter unicode61 remove_diacritics 1');
                CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN
                  INSERT INTO mem_fts(rowid, text) VALUES (new.id, new.text);
                END;
                CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memories BEGIN
                  INSERT INTO mem_fts(mem_fts, rowid, text) VALUES('delete', old.id, old.text);
                END;
                CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE ON memories BEGIN
                  INSERT INTO mem_fts(mem_fts, rowid, text) VALUES('delete', old.id, old.text);
                  INSERT INTO mem_fts(rowid, text) VALUES (new.id, new.text);
                END;
                """)
            self.conn.commit()
            row = self.conn.execute("select value from meta where key='version'").fetchone()
            if row is None:
                self.conn.execute("insert into meta values('version','1')")
                self.conn.commit()

    # ---- store ---------------------------------------------------------------

    def version(self) -> int:
        with self.lock:
            return int(self.conn.execute("select value from meta where key='version'").fetchone()[0])

    def _bump(self) -> None:
        self.conn.execute("update meta set value = cast(cast(value as integer) + 1 as text) where key='version'")

    def remember(self, text: str, scope: str = USER_SCOPE, source: str = "manual",
                 confidence: float | None = None, provenance: dict | None = None) -> dict:
        """Store one durable fact. An active row with the same normalized text and scope is updated instead
        of duplicated: the curation loop may propose the same thing many times."""
        text = " ".join(str(text).split())
        if not text:
            raise ValueError("memory text is empty")
        if len(text) > 4000:
            raise ValueError("memory text is over 4000 characters - split it into separate facts")
        scope = scope_name(scope, USER_SCOPE)
        conf = 0.8 if confidence is None else max(0.0, min(1.0, float(confidence)))
        now = time.time()
        with self.lock:
            same = self.conn.execute("select id, text from memories where scope=?", (scope,)).fetchall()
            hit = next((r for r in same if norm_text(r[1]) == norm_text(text)), None)
            if hit:
                self.conn.execute("update memories set updated_at=?, confidence=max(confidence,?) where id=?",
                                  (now, conf, hit[0]))
                self.conn.commit()
                return {"id": hit[0], "deduped": True}
            cur = self.conn.execute(
                "insert into memories(scope,text,source,provenance,confidence,created_at,updated_at)"
                " values(?,?,?,?,?,?,?)",
                (scope, text, str(source)[:40], json.dumps(provenance, ensure_ascii=False) if provenance else None,
                 conf, now, now))
            self._bump()
            self.conn.commit()
            return {"id": cur.lastrowid, "deduped": False}

    def forget(self, memory_id: int) -> bool:
        with self.lock:
            gone = self.conn.execute("delete from memories where id=?", (int(memory_id),))
            self._bump()
            self.conn.commit()
            return gone.rowcount > 0

    def list_memories(self, scope: str | None = None, limit: int = 100) -> list[dict]:
        with self.lock:
            rows = (self.conn.execute(
                "select id,scope,text,source,confidence,created_at,updated_at,hits from memories"
                " where scope=? order by updated_at desc limit ?", (scope, max(1, min(500, int(limit))))).fetchall()
                if scope else self.conn.execute(
                "select id,scope,text,source,confidence,created_at,updated_at,hits from memories"
                " order by updated_at desc limit ?", (max(1, min(500, int(limit))),)).fetchall())
        return [dict(id=r[0], scope=r[1], text=r[2], source=r[3], confidence=r[4],
                     created_at=round(r[5], 1), updated_at=round(r[6], 1), hits=r[7]) for r in rows]

    # ---- recall --------------------------------------------------------------

    def search(self, query: str, scope: str = DEFAULT_PROJECT, limit: int | None = None) -> list[dict]:
        """FTS5 bm25 candidates over the user scope plus this request's project scope, then re-ordered by a
        blend: 0.75 rank + 0.25 recency + 0.10 confidence, all in [0,1]-ish. Returns at most `limit`."""
        limit = self.recall_limit if limit is None else max(1, min(50, int(limit)))
        words = WORD_RE.findall(query.lower())[:MAX_QUERY_WORDS]
        if not words:
            return []
        match = " OR ".join(f'"{w}"' for w in words)
        scopes, params = [USER_SCOPE], [match]
        s = scope_name(scope)
        if s != USER_SCOPE:
            scopes.append(s)
        params.append(limit * 3)
        with self.lock:
            rows = self.conn.execute(
                f"select m.id, m.scope, m.text, m.confidence, m.updated_at, bm25(mem_fts) as r"
                " from mem_fts join memories m on m.id = mem_fts.rowid"
                " where mem_fts match ? and m.scope in (" + ",".join("?" * len(scopes)) + ")"
                " order by r limit ?", [match] + scopes + [limit * 3]).fetchall()
        if not rows:
            return []
        k = len(rows)
        now = time.time()
        out = []
        for pos, r in enumerate(rows):
            fresh = math.exp(-max(0.0, now - r[4]) / 86400.0 / AGE_SCALE_DAYS)
            score = 0.75 * ((k - pos) / k) + 0.25 * fresh + 0.10 * r[3]
            out.append(dict(id=r[0], scope=r[1], text=r[2], score=round(score, 4)))
        out.sort(key=lambda x: -x["score"])
        hits = out[:limit]
        if hits:
            self._seen([h["id"] for h in hits])
        return hits

    def _seen(self, ids: list[int]) -> None:
        now = time.time()
        with self.lock:
            self.conn.executemany("update memories set hits=hits+1, last_seen_at=? where id=?",
                                  [(now, i) for i in ids])
            self.conn.commit()

    def _render_block(self, hits: list[dict], budget: int, scope: str) -> tuple[str, list[dict]]:
        """One fact block, byte-shaped identically for head and tail use: header + instruction line +
        one "[n] (scope) text" line per fact, kept under `budget` characters. Returns the block and the
        hits it ACTUALLY shows - what a later request dedups against and what the journal credits must
        be the lines the model really read, not everything the search returned."""
        lines = [f'<server_memory version="{self.version()}" scope="{scope}">',
                 "Long-term memory of this user, kept by the inference server and shared by all their"
                 " applications. Use it silently when relevant - never mention these notes, this tag, or"
                 " 'remembering' to the user; if a note conflicts with what the user says now, they win."]
        lines += [f"[{i + 1}] ({h['scope']}) {h['text']}" for i, h in enumerate(hits)]
        lines.append("</server_memory>")
        trimmed, used = [], len(lines[0]) + len(lines[-1]) + 120          # the header and the instruction line cost
        shown = []
        for h, line in zip(hits, lines[2:-1]):
            used += len(line) + 1
            if used > budget:
                break
            trimmed.append(line)
            shown.append(h)
        return "\n".join([lines[0], lines[1]] + trimmed + [lines[-1]]), shown

    def block_for(self, messages: list[dict], scope: str) -> tuple[str | None, list[dict], str]:
        """The hidden system block for this conversation - or (None, [], "") when there is no user text to
        recall with. Pinned per (scope, first user message): byte-stable while cached, fresh bytes for a
        new conversation. The query is the last user message; the search also uses the first one when the
        conversation's newest text is a tool result. The returned hits are the facts the block SHOWS
        (id-only dicts once pinned) - per-turn fresh recall is `turn_block_for`, not this."""
        users = [text_of(m.get("content")) for m in messages if isinstance(m, dict) and m.get("role") == "user"]
        users = [u for u in users if u.strip()]
        if not users:
            return None, [], ""
        scope = scope_name(scope)
        key = (scope, "h" + hashlib.sha1(norm_text(users[0]).encode("utf-8", "replace")).hexdigest()[:16])
        query = users[-1][:400] if len(users[-1].strip()) > 3 else users[0][:400]
        pinned = self._blocks.get(key)
        if pinned is not None:
            self._blocks.move_to_end(key)
            block, ids = pinned
            return block, [{"id": i} for i in ids], query
        hits = self.search(query, scope=scope)
        if not hits:
            return None, [], query
        block, shown = self._render_block(hits, self.max_block_chars, scope)
        self._blocks[key] = (block, [h["id"] for h in shown])
        while len(self._blocks) > self._cache_entries:
            self._blocks.popitem(last=False)
        return block, shown, query

    def turn_block_for(self, messages: list[dict], scope: str,
                       exclude_ids: list[int] | tuple[int, ...] = ()):
        """Fresh recall for the conversation's NEWEST user message, minus the facts the pinned head
        block already carries -> (block | None, shown hits). It is merged into that message rather than
        the system head (see request_state): a 184k-token agent session reuses ~99.9% of its prompt
        bytes every turn (dev log: "prompt 183,987 tokens = 183,812 reused + 175 read"), so touching
        the head costs a full re-read of the conversation, while the tail is re-sent anyway. The
        conversation's first turn gets no tail - the head block was computed from that same message -
        and a request whose newest text is too short to query with gets none either. Cached like the
        head block, keyed by (scope, newest message, the exclusion set), so a retried turn replays
        byte-identically."""
        if not self.per_turn:
            return None, []
        users = [text_of(m.get("content")) for m in messages if isinstance(m, dict) and m.get("role") == "user"]
        users = [u for u in users if u.strip()]
        if len(users) < 2 or len(users[-1].strip()) <= 3:
            return None, []
        scope = scope_name(scope)
        seen = sorted(set(int(i) for i in exclude_ids or ()))
        material = f"{norm_text(users[-1][:400])}|{','.join(map(str, seen))}"
        key = (scope, "t" + hashlib.sha1(material.encode("utf-8", "replace")).hexdigest()[:16])
        pinned = self._blocks.get(key)
        if pinned is not None:
            self._blocks.move_to_end(key)
            block, ids = pinned
            return (block or None), [{"id": i} for i in ids]
        hits = [h for h in self.search(users[-1][:400], scope=scope) if h["id"] not in set(seen)]
        hits = hits[:self.turn_recall_limit]
        if not hits:
            self._blocks[key] = ("", [])            # the miss is pinned too: same message, same nothing
            return None, []
        block, shown = self._render_block(hits, self.turn_block_chars, scope)
        if not shown:
            self._blocks[key] = ("", [])            # nothing fits the budget: inject no bytes at all
            return None, []
        self._blocks[key] = (block, [h["id"] for h in shown])
        while len(self._blocks) > self._cache_entries:
            self._blocks.popitem(last=False)
        return block, shown

    # ---- journal (raw turns; the curation loop reads these) -------------------

    def journal_turn(self, scope: str, user_text: str, assistant_text: str, model: str | None = None) -> int:
        with self.lock:
            cur = self.conn.execute(
                "insert into turns(scope,user_text,assistant_text,model,created_at) values(?,?,?,?,?)",
                (scope_name(scope), user_text[:8000], assistant_text[:16000],
                 (model or "")[:80] or None, time.time()))
            self.conn.commit()
            return cur.lastrowid

    def pending_turns(self, limit: int = 200) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "select id,scope,user_text,assistant_text,created_at from turns where processed=0"
                " order by id limit ?", (max(1, min(1000, int(limit))),)).fetchall()
        return [dict(id=r[0], scope=r[1], user=r[2][:2000], assistant=r[3][:4000],
                     created_at=round(r[4], 1)) for r in rows]

    def mark_processed(self, ids: list[int]) -> None:
        if not ids:
            return
        with self.lock:
            self.conn.executemany("update turns set processed=1 where id=?", [(int(i),) for i in ids])
            self.conn.commit()

    # ---- stats ----------------------------------------------------------------

    def stats(self) -> dict:
        with self.lock:
            memories = self.conn.execute("select count(*) from memories").fetchone()[0]
            by_scope = dict(self.conn.execute("select scope, count(*) from memories group by scope").fetchall())
            pending = self.conn.execute("select count(*) from turns where processed=0").fetchone()[0]
            turns = self.conn.execute("select count(*) from turns").fetchone()[0]
        return {"version": self.version(), "memories": memories, "by_scope": by_scope,
                "turns": turns, "turns_pending": pending, "inject": self.inject,
                "recall_limit": self.recall_limit, "max_block_chars": self.max_block_chars,
                "per_turn": self.per_turn, "turn_recall_limit": self.turn_recall_limit,
                "turn_block_chars": self.turn_block_chars,
                "pinned_blocks": len(self._blocks), "db": self.db_path}

    def close(self) -> None:
        with self.lock:
            self.conn.close()


# ---- server glue ---------------------------------------------------------------

def request_state(mem: "MemoryService | None", messages: list[dict], headers, req: dict) -> tuple[list[dict], dict | None]:
    """/v1/chat/completions' memory step: (messages, ctx). With memory off or muted
    (`X-Strata-Memory: off`), the messages come back untouched and ctx is None -
    the byte-identical upstream path. With nothing recalled, the messages still come back
    untouched but ctx is built (unless the request has no user text): the exchange journals
    anyway, which is how a fresh scope bootstraps. Otherwise a hidden system block is
    prepended (pinned per
    conversation), the conversation's newest user message may carry a fresh per-turn block of facts the
    head does not have (see turn_block_for), and ctx carries what to journal when the answer completes.
    The caller's project scope is `X-Strata-Memory-Scope`."""
    if mem is None:
        return messages, None
    if (headers.get("X-Strata-Memory") or "").strip().lower() in ("off", "mute", "0"):
        return messages, None
    scope = scope_name(headers.get("X-Strata-Memory-Scope"))
    block, hits, query = mem.block_for(messages, scope)
    users = [text_of(m.get("content")) for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    last_user = next((u for u in reversed(users) if u.strip()), "")
    if block is None:
        # journal even when nothing was recalled: a quiet turn today is tomorrow's curation source,
        # and a scope with no facts yet could never bootstrap otherwise. No bytes join the prompt -
        # only ctx is built, and only when there is a user turn worth curating (a tool-continuation
        # request with no user text journals an answer nobody asked for: skip it).
        if not last_user.strip():
            return messages, None
        return messages, {"scope": scope, "user": last_user, "model": req.get("model"), "query": query,
                          "hits": []}
    tblock, thits = mem.turn_block_for(messages, scope, [h["id"] for h in hits])
    ctx = {"scope": scope, "user": last_user, "model": req.get("model"), "query": query,
           "hits": [h["id"] for h in hits] + [h["id"] for h in thits]}
    # The Qwen chat template allows ONE system message, at index 0 ("System message must be at the
    # beginning"), and openai_to_messages has already folded "developer" into system and turned late
    # systems into user - so the block cannot ride as a second system turn. When the client sent its
    # own leading system (OpenCode, Claude Code, ...), merge ours in front of ITS text instead of
    # prepending: the merged head keeps the block's stable bytes at the very front of the prompt,
    # the template sees exactly one system, and the client's instruction is untouched in substance.
    head = messages[0] if messages and isinstance(messages[0], dict) else None
    if head is not None and head.get("role") in ("system", "developer"):
        c = head.get("content")
        if isinstance(c, list):
            merged = dict(head, content=[{"type": "text", "text": block}] + list(c))
        elif isinstance(c, str) and c.strip():
            merged = dict(head, content=f"{block}\n\n{c}")
        else:
            merged = dict(head, content=block)
        out = [merged] + list(messages[1:])
    else:
        out = [{"role": "system", "content": block}] + list(messages)
    # The per-turn block rides the tail, NOT the head: merged into the newest user message, whose bytes
    # are new to the prompt this turn anyway, so the conversation's reused prefix keeps every checkpoint
    # byte it had. The template's one-system rule is untouched - this rides inside a user turn.
    if tblock is not None:
        i = next((j for j in range(len(out) - 1, -1, -1)
                  if isinstance(out[j], dict) and out[j].get("role") == "user"
                  and text_of(out[j].get("content")).strip()), None)
        if i is not None:
            c = out[i].get("content")
            if isinstance(c, list):
                out[i] = dict(out[i], content=[{"type": "text", "text": tblock}] + list(c))
            else:
                out[i] = dict(out[i], content=f"{tblock}\n\n{c}")
    return out, ctx


def journal_chunks(chunks, mem: "MemoryService", ctx: dict):
    """Wrap the OpenAI chunk generator to collect the answer's text and journal it as one turn. Transparent:
    every item passes through unchanged and on the same schedule; journaling rides in finally, and a store
    error never breaks an answer the user already watched."""
    parts: list[str] = []
    try:
        for c in chunks:
            if isinstance(c, dict):
                try:
                    delta = (c.get("choices") or [{}])[0].get("delta") or {}
                    if isinstance(delta.get("content"), str):
                        parts.append(delta["content"])
                except (KeyError, IndexError, TypeError, AttributeError):
                    pass
            yield c
    finally:
        text = "".join(parts).strip()
        if text:
            try:
                mem.journal_turn(ctx["scope"], ctx["user"], text, ctx.get("model"))
            except Exception:      # noqa: BLE001 - the journal is never allowed to break a reply
                pass
