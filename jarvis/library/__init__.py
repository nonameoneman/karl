"""Офлайн-библиотека знаний Jarvis: лучшие признанные источники мира, скачанные локально.

Каталог (library/catalog.json) → загрузка с докачкой и проверкой контрольной суммы первоисточника → хранилище
по хэшу (папка библиотеки) → поиск с точным источником (тексты — FTS5 + векторы, ZIM Kiwix — встроенный индекс).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .. import bus
from ..tools import tool
from . import index, store, zim

CATALOG = Path(__file__).resolve().parents[2] / "library" / "catalog.json"
_jobs: dict[str, str] = {}


CATALOG_LOCAL = CATALOG.parents[1] / "data" / "catalog.local.json"   # свои источники: карты региона, языки


def catalog() -> list[dict]:
    out: list[dict] = []
    for p in (CATALOG, CATALOG_LOCAL):
        try:
            out += json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return out


def add_entry(entry: dict) -> str:
    """Скачать элемент каталога, проверить, положить в хранилище, внести в список, проиндексировать."""
    def prog(done, total):
        _jobs[entry["id"]] = f"{done / 1048576:.0f} из {total / 1048576:.0f} МБ" if total else f"{done / 1048576:.0f} МБ"

    sha, size, how = store.download_entry(entry, prog)
    item = {k: entry.get(k) for k in ("id", "tier", "bundle", "category", "title", "short", "lang", "format", "parser",
                                      "url", "checksum_url", "license", "version_date")}
    item.update(sha256=sha, size=size, verified=how, added=time.strftime("%Y-%m-%d"))
    m = store.load_manifest()
    old = next((i for i in m["items"] if i["id"] == item["id"]), None)
    m["items"] = [i for i in m["items"] if i["id"] != item["id"]] + [item]
    m["version"] = m.get("version", 0) + 1
    store.save_manifest(m)
    if old and old.get("sha256") != sha and store.has_blob(old.get("sha256", "")):
        store.blob_path(old["sha256"]).unlink(missing_ok=True)       # старая версия больше не нужна
    n = index.index_item(item) if item.get("format") != "zim" else 0
    _jobs[entry["id"]] = f"готово: {size / 1048576:.1f} МБ, {how}" + (f", {n} фрагментов в поиске" if n else "")
    return _jobs[entry["id"]]


def _scope(where: str) -> list[str] | None:
    if not where:
        return None
    w = where.lower()
    return [i["id"] for i in store.load_manifest()["items"]
            if w in i["id"].lower() or w in (i.get("title") or "").lower() or w in (i.get("category") or "").lower()]


# ─── инструменты Jarvis ───────────────────────────────────────────────────

@tool("green")
def library_search(query: str, where: str = "") -> str:
    """Офлайн-библиотека знаний (работает без интернета): выживание и первая помощь, медицина, вода/еда/энергия,
    сельское хозяйство, ремесла, животные, растения, грибы, геология, астрономия, языки и словари, священные
    тексты (Библия, Тора, Коран, Гита…), учебники, Википедия. Для вопросов о знаниях, «как сделать», цитат —
    раньше web_search. Отвечай ТОЛЬКО по найденному, цитаты дословно, всегда называй источник и место.

    Args:
        query: вопрос или ключевые слова
        where: необязательно — источник или раздел (Библия, Коран, медицина, Википедия, растения)
    """
    import re as _re
    scope = _scope(where)
    en = ""
    if _re.search(r"[а-яё]", query.lower()) and len(query.split()) > 2:
        try:                                          # половина практической библиотеки англоязычная
            from ..llm import complete
            en = complete(f"Translate to English, output only the translation: {query}").strip().strip('"')[:200]
        except Exception:
            en = ""
    hits = index.search(query, k=5, items=scope, extra_query=en) if index.indexed_items() else []
    hits += zim.search(query, k=2, item_ids=scope)
    if en:
        hits += zim.search(en, k=1, item_ids=scope)
    if not hits:
        return "В офлайн-библиотеке ничего не нашёл." + ("" if store.load_manifest()["items"] else " Библиотека пуста.")
    out = [f"[{h['ref']}]\n{h['text'][:800]}" for h in hits[:5]]
    return ("<library>\n" + "\n\n".join(out) +
            "\n</library>\nОтвечай по этим фрагментам и назови источник в квадратных скобках.")


@tool("green")
def library_status() -> str:
    """Что есть в офлайн-библиотеке, сколько весит, что качается и что ещё можно скачать."""
    m = store.load_manifest()
    have = sorted(m["items"], key=lambda x: (x.get("bundle") or "", x.get("category") or "", x["id"]))
    total = sum(i.get("size", 0) for i in have) / 1073741824
    lines = [f"В библиотеке {len(have)} источников, {total:.1f} ГБ."]
    for i in have:
        lines.append(f"• {i['title']} — {i.get('size', 0) / 1048576:.0f} МБ, версия {i.get('version_date') or '?'}, {i.get('verified')}")
    for k, v in _jobs.items():
        if not v.startswith("готово"):
            lines.append(f"⇣ {k}: {v}")
    missing = [c for c in catalog() if not any(i["id"] == c["id"] for i in have)]
    if missing:
        size = sum(c.get("size_mb") or 0 for c in missing) / 1024
        lines.append(f"Ещё не скачано {len(missing)} ({size:.0f} ГБ): " +
                     ", ".join(f"{c['id']}" for c in missing[:20]) + (" …" if len(missing) > 20 else ""))
    return "\n".join(lines)


@tool("yellow")
def library_get(ids: list[str]) -> str:
    """Скачать источники из каталога в офлайн-библиотеку (в фоне, с проверкой контрольных сумм первоисточника,
    с докачкой после обрыва). Также обновляет уже скачанное до свежей версии из каталога.

    Args:
        ids: id из каталога; ["canon"] — канон (главное, ~6 ГБ), ["essential"] — с Википедией, ["full"] — всё
    """
    cat = {c["id"]: c for c in catalog()}
    if ids == ["canon"]:
        want = [c for c in cat.values() if c.get("canon")]
    elif ids in (["essential"], ["full"]):
        want = [c for c in cat.values() if c.get("bundle") == "essential" or ids == ["full"]]
    elif ids == ["core"]:
        want = [c for c in cat.values() if c.get("tier") == "core"]
    else:
        want = [cat[i] for i in ids if i in cat]
    have = {i["id"]: i for i in store.load_manifest()["items"]}
    want = [c for c in want if c["id"] not in have or (c.get("version_date") or "") > (have[c["id"]].get("version_date") or "")]
    if not want:
        return "Всё уже скачано и актуально (или нет таких id в каталоге)."

    def job():
        for c in want:
            _jobs[c["id"]] = "в очереди"
        for c in sorted(want, key=lambda c: c.get("size_mb") or 0):      # сначала маленькое и важное
            try:
                res = add_entry(c)
                bus.publish({"type": "status", "text": f"Библиотека: {c['title']} — {res}"})
            except Exception as e:
                _jobs[c["id"]] = f"ошибка: {e}"
                bus.publish({"type": "error", "text": f"Библиотека: {c['title']}: {e}"})
    threading.Thread(target=job, daemon=True, name="library-get").start()
    total = sum(c.get("size_mb") or 0 for c in want)
    return f"Начал скачивать {len(want)} источник(ов), ~{total / 1024:.1f} ГБ. Прогресс — library_status."


from . import map_tools  # noqa: E402,F401 — инструменты офлайн-карт
