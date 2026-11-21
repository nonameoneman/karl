"""Журнал действий с откатом.

Каждое изменение файловой системы записывается ДО выполнения (status=pending),
затем помечается done. Удаление = перенос в data/backups/trash, поэтому обратимо.
Действия группируются по «ходу» (turn_id) — «верни как было» откатывает весь ход.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from .config import DATA_DIR, BACKUP_DIR

JOURNAL = DATA_DIR / "journal.jsonl"
_lock = threading.Lock()
_current_turn = "manual"


def set_turn(turn_id: str) -> None:
    global _current_turn
    _current_turn = turn_id


def current_turn() -> str:
    return _current_turn


def _append(rec: dict) -> None:
    with _lock, JOURNAL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _read() -> list[dict]:
    if not JOURNAL.exists():
        return []
    out = []
    for line in JOURNAL.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _unique(dst: Path) -> Path:
    if not dst.exists():
        return dst
    i = 1
    while True:
        cand = dst.with_name(f"{dst.stem} ({i}){dst.suffix}")
        if not cand.exists():
            return cand
        i += 1


def record(op: str, **data) -> str:
    rid = uuid.uuid4().hex[:10]
    _append({"id": rid, "turn": _current_turn, "ts": time.time(), "op": op, "status": "pending", **data})
    return rid


def done(rid: str, **data) -> None:
    _append({"id": rid, "status": "done", **data})


# ─── Операции, которые используют инструменты ──────────────────────────────

def move(src: Path, dst: Path) -> Path:
    src, dst = Path(src), Path(dst)
    if dst.exists() and dst.is_dir():
        dst = dst / src.name
    dst = _unique(dst)
    if not dst.parent.exists():
        mkdir(dst.parent)
    rid = record("move", src=str(src), dst=str(dst))
    shutil.move(str(src), str(dst))
    done(rid)
    return dst


def write(path: Path, content: str, append: bool = False) -> None:
    path = Path(path)
    backup = None
    if path.exists():
        backup = BACKUP_DIR / f"{uuid.uuid4().hex[:10]}_{path.name}"
        shutil.copy2(path, backup)
    rid = record("write", path=str(path), backup=str(backup) if backup else None)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    done(rid)


def mkdir(path: Path) -> None:
    path = Path(path)
    if path.exists():
        return
    rid = record("mkdir", path=str(path))
    path.mkdir(parents=True)
    done(rid)


def delete(path: Path) -> Path:
    """Мягкое удаление: перенос в корзину Russel."""
    path = Path(path)
    trash = BACKUP_DIR / "trash" / f"{uuid.uuid4().hex[:10]}_{path.name}"
    trash.parent.mkdir(parents=True, exist_ok=True)
    rid = record("delete", path=str(path), trash=str(trash))
    shutil.move(str(path), str(trash))
    done(rid)
    return trash


# ─── Откат ─────────────────────────────────────────────────────────────────

def _completed() -> list[dict]:
    recs = _read()
    by_id: dict[str, dict] = {}
    for r in recs:
        if r.get("op"):
            by_id[r["id"]] = dict(r)
        elif r["id"] in by_id:
            by_id[r["id"]].update(r)
    undone = {r["undo_of"] for r in recs if r.get("op") == "undo" and r.get("undo_of")}
    return [r for r in by_id.values() if r["status"] == "done" and r["op"] != "undo" and r["id"] not in undone]


def history(limit: int = 20) -> list[dict]:
    return _completed()[-limit:]


def turns() -> list[str]:
    seen: list[str] = []
    for r in _completed():
        if r["turn"] not in seen:
            seen.append(r["turn"])
    return seen


def undo_turn(turn_id: str | None = None) -> list[str]:
    """Откатывает все действия хода (по умолчанию — последнего хода с изменениями)."""
    ts = turns()
    if not ts:
        return ["Нечего откатывать."]
    turn_id = turn_id or ts[-1]
    ops = [r for r in _completed() if r["turn"] == turn_id]
    report = []
    for r in reversed(ops):
        try:
            if r["op"] == "move":
                src, dst = Path(r["src"]), Path(r["dst"])
                if not dst.exists():
                    report.append(f"⚠ {dst} уже нет — пропускаю")
                    continue
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst), str(_unique(src)))
                report.append(f"↩ {dst.name} → {src.parent}")
            elif r["op"] == "write":
                p = Path(r["path"])
                if r.get("backup"):
                    shutil.copy2(r["backup"], p)
                    report.append(f"↩ восстановлен {p.name}")
                elif p.exists():
                    p.unlink()
                    report.append(f"↩ удалён созданный {p.name}")
            elif r["op"] == "mkdir":
                p = Path(r["path"])
                if p.exists() and not any(p.iterdir()):
                    p.rmdir()
                    report.append(f"↩ удалена пустая папка {p.name}")
            elif r["op"] == "delete":
                p = Path(r["path"])
                shutil.move(r["trash"], str(_unique(p)))
                report.append(f"↩ возвращён {p.name}")
            else:
                continue
            _append({"id": uuid.uuid4().hex[:10], "turn": "undo", "ts": time.time(),
                     "op": "undo", "undo_of": r["id"], "status": "done"})
        except Exception as e:
            report.append(f"✗ {r['op']} {r.get('path') or r.get('dst')}: {e}")
    return report or ["В этом ходе не было изменений файлов."]
