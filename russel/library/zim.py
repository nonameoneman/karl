"""Поиск по ZIM-файлам Kiwix (Википедия, медицина, руководства по выживанию) без векторизации:
у каждого ZIM есть свой полнотекстовый индекс (Xapian)."""
from __future__ import annotations

import html
import re
import threading

from .store import blob_path, load_manifest

_open: dict[str, object] = {}
_lock = threading.Lock()


def _archive(sha: str):
    from libzim.reader import Archive
    with _lock:
        if sha not in _open:
            _open[sha] = Archive(str(blob_path(sha)))
        return _open[sha]


def _text(html_bytes: bytes, limit: int) -> str:
    t = html_bytes.decode("utf-8", errors="ignore")
    t = re.sub(r"(?is)<(script|style|table|figure|nav|footer).*?</\1>", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html.unescape(re.sub(r"\s+", " ", t)).strip()
    return t[:limit]


def zim_items() -> list[dict]:
    return [i for i in load_manifest()["items"] if i.get("format") == "zim" and blob_path(i.get("sha256", "")).exists()]


def search(query: str, k: int = 4, item_ids: list[str] | None = None, chars: int = 1500) -> list[dict]:
    from libzim.search import Query, Searcher
    out = []
    for it in zim_items():
        if item_ids and it["id"] not in item_ids:
            continue
        try:
            a = _archive(it["sha256"])
            if not a.has_fulltext_index:
                continue
            res = Searcher(a).search(Query().set_query(query))
            for path in list(res.getResults(0, k)):
                e = a.get_entry_by_path(path)
                txt = _text(bytes(e.get_item().content), chars)
                out.append({"item": it["id"], "title": it["title"], "ref": f"{it.get('short') or it['title']}: {e.title}",
                            "text": txt, "path": path})
        except Exception as ex:
            out.append({"item": it["id"], "title": it["title"], "ref": "ошибка", "text": str(ex)})
    return out


def article(item_id: str, path_or_title: str, chars: int = 6000) -> str:
    it = next((i for i in zim_items() if i["id"] == item_id), None)
    if not it:
        return "Нет такого ZIM."
    a = _archive(it["sha256"])
    try:
        e = a.get_entry_by_path(path_or_title)
    except KeyError:
        e = a.get_entry_by_title(path_or_title)
    return _text(bytes(e.get_item().content), chars)
