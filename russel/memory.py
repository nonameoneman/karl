"""Долгая память: факты о пользователе (FTS5) и история диалогов. SQLite в data/memory.db."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time

from .config import DATA_DIR

DB = DATA_DIR / "memory.db"
_lock = threading.Lock()
_conn = sqlite3.connect(DB, check_same_thread=False)
_conn.executescript("""
CREATE VIRTUAL TABLE IF NOT EXISTS facts USING fts5(text, tags, ts UNINDEXED, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, session TEXT, role TEXT, content TEXT, ts REAL);
""")
_conn.commit()


def add_fact(text: str, tags: str = "") -> int:
    with _lock:
        cur = _conn.execute("INSERT INTO facts(text, tags, ts) VALUES (?,?,?)", (text.strip(), tags, time.time()))
        _conn.commit()
        return cur.lastrowid


def _fts_query(q: str) -> str:
    words = [w for w in re.findall(r"\w+", q.lower()) if len(w) > 2]
    # префиксный поиск по основам слов: «документы» найдёт «документов»
    return " OR ".join(f'"{w[:max(4, len(w) - 2)]}"*' for w in words[:12])


def search_facts(q: str, limit: int = 6) -> list[tuple[int, str]]:
    fq = _fts_query(q)
    if not fq:
        return []
    with _lock:
        try:
            rows = _conn.execute("SELECT rowid, text FROM facts WHERE facts MATCH ? ORDER BY rank LIMIT ?",
                                 (fq, limit)).fetchall()
        except sqlite3.OperationalError:
            rows = []
    return rows


def all_facts(limit: int = 200) -> list[tuple[int, str]]:
    with _lock:
        return _conn.execute("SELECT rowid, text FROM facts ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()


def delete_fact(fact_id: int) -> bool:
    with _lock:
        cur = _conn.execute("DELETE FROM facts WHERE rowid=?", (fact_id,))
        _conn.commit()
        return cur.rowcount > 0


def log_message(session: str, role: str, content) -> None:
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    with _lock:
        _conn.execute("INSERT INTO messages(session, role, content, ts) VALUES (?,?,?,?)",
                      (session, role, content, time.time()))
        _conn.commit()


def recent_messages(limit: int = 30) -> list[dict]:
    with _lock:
        rows = _conn.execute("SELECT role, content, ts FROM messages WHERE role IN ('user','assistant') "
                             "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"role": r, "content": c, "ts": t} for r, c, t in reversed(rows)]
