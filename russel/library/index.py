"""Индекс библиотеки: фрагменты с точным адресом (книга глава:стих, сура:аят, страница) → FTS5 + векторы bge-m3.

Поиск гибридный: по точным словам (FTS5, bm25) и по смыслу (косинус векторов), слияние по Reciprocal Rank Fusion.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Iterator

import numpy as np

from .store import LIB_DIR, blob_path

DB_PATH = LIB_DIR / "index" / "library.db"
VEC_DIR = LIB_DIR / "index" / "vectors"
VEC_DIR.mkdir(parents=True, exist_ok=True)
EMBED_MODEL = "bge-m3"
_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.executescript("""
CREATE TABLE IF NOT EXISTS passages(id INTEGER PRIMARY KEY, item TEXT, ref TEXT, text TEXT, lang TEXT);
CREATE INDEX IF NOT EXISTS passages_item ON passages(item);
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(text, ref, content='passages', content_rowid='id',
                                                          tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, title TEXT, sha256 TEXT, lang TEXT, tier TEXT, n INTEGER);
""")
_conn.commit()
_vec_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}


# ─── разбор форматов в фрагменты (ref, text) ──────────────────────────────

def _split(text: str, size: int = 900) -> Iterator[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    buf = ""
    for p in paras:
        if len(buf) + len(p) > size and buf:
            yield buf
            buf = ""
        while len(p) > size * 1.5:
            cut = p.rfind(". ", 0, size) + 1 or size
            yield p[:cut]
            p = p[cut:].strip()
        buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        yield buf


def parse_plain(path: Path, title: str) -> Iterator[tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    for i, chunk in enumerate(_split(text), 1):
        yield f"{title}, фрагмент {i}", chunk


def parse_pdf(path: Path, title: str) -> Iterator[tuple[str, str]]:
    from pypdf import PdfReader
    for n, page in enumerate(PdfReader(str(path)).pages, 1):
        t = (page.extract_text() or "").strip()
        for j, chunk in enumerate(_split(t), 1):
            yield f"{title}, стр. {n}" + (f" ({j})" if j > 1 else ""), chunk


def parse_verses(path: Path, title: str) -> Iterator[tuple[str, str]]:
    """Универсальный разбор священных текстов в JSON:
      [{"book": "Бытие", "chapter": 1, "verse": 1, "text": "..."}, ...]   или
      {"books": [{"name": "...", "chapters": [["стих1", "стих2"], ...]}]} или
      [{"name"/"abbrev": "...", "chapters": [[...], ...]}]  (формат thiagobodruk/bible)
    Стихи группируются по 3–5, чтобы у фрагмента был контекст, а адрес остаётся точным."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    rows: list[tuple[str, int, int, str]] = []
    if isinstance(data, dict) and "books" in data:
        data = data["books"]
    if isinstance(data, list) and data and isinstance(data[0], dict) and "chapters" in data[0]:
        for b in data:
            name = b.get("name") or b.get("book") or b.get("abbrev")
            for ci, ch in enumerate(b["chapters"], 1):
                for vi, v in enumerate(ch, 1):
                    rows.append((name, ci, vi, v if isinstance(v, str) else v.get("text", "")))
    elif isinstance(data, list):
        for r in data:
            rows.append((r.get("book") or r.get("sura_name") or r.get("sura"), int(r.get("chapter") or r.get("sura") or 0),
                         int(r.get("verse") or r.get("aya") or 0), r.get("text", "")))
    yield from _group_verses(rows, title)


def parse_tanzil(path: Path, title: str) -> Iterator[tuple[str, str]]:
    """tanzil.net: строки «сура|аят|текст»."""
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[0].isdigit():
            rows.append(("Сура", int(parts[0]), int(parts[1]), parts[2]))
    yield from _group_verses(rows, title, sep=":")


def _group_verses(rows, title, group: int = 4, sep: str = ":") -> Iterator[tuple[str, str]]:
    i = 0
    while i < len(rows):
        book, ch, v0, _ = rows[i]
        block = [rows[i]]
        j = i + 1
        while j < len(rows) and len(block) < group and rows[j][0] == book and rows[j][1] == ch:
            block.append(rows[j])
            j += 1
        v1 = block[-1][2]
        ref = f"{title}: {book} {ch}{sep}{v0}" + (f"–{v1}" if v1 != v0 else "")
        text = " ".join(f"[{r[2]}] {r[3].strip()}" for r in block)
        yield ref, text
        i = j


_USFM_SKIP = {"FRT", "BAK", "GLO", "INT", "OTH", "CNC", "TDX", "NDX", "XXA", "XXB", "XXC", "XXD", "XXE", "XXF", "XXG"}


def _usfm_clean(t: str) -> str:
    t = re.sub(r"\\f\s.*?\\f\*", "", t, flags=re.S)                      # сноски
    t = re.sub(r"\\x\s.*?\\x\*", "", t, flags=re.S)                      # перекрёстные ссылки
    t = re.sub(r"\\\+?w\s+([^|\\]*?)(?:\|[^\\]*)?\\\+?w\*", r"\1", t)     # \w слово|strong="…"\w*
    t = re.sub(r"\\[a-z]+\d*\*?", " ", t)                                # прочие маркеры
    return re.sub(r"\s+", " ", t).strip()


def parse_usfm_zip(path: Path, title: str) -> Iterator[tuple[str, str]]:
    """eBible USFM: \\id КНИГА, \\h Название, \\c глава, \\v стих. Адрес: «Библия: Иоанн 3:16»."""
    import zipfile
    rows = []
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if n.lower().endswith((".usfm", ".sfm"))):
            src = z.read(name).decode("utf-8-sig", errors="ignore")
            m = re.search(r"\\id\s+(\w{3})", src)
            if not m or m.group(1).upper() in _USFM_SKIP:
                continue
            book = (re.search(r"\\h\s+(.+)", src) or re.search(r"\\toc2\s+(.+)", src) or m).group(1).strip()
            ch = 0
            for part in re.split(r"(\\c\s+\d+|\\v\s+\d+[\w-]*)", src):
                if part.startswith("\\c"):
                    ch = int(part.split()[1])
                elif part.startswith("\\v"):
                    cur_v = int(re.match(r"\d+", part.split()[1]).group(0))
                    rows.append([book, ch, cur_v, ""])
                elif rows and rows[-1][0] == book and rows[-1][1] == ch:
                    rows[-1][3] += part
    rows = [(b, c, v, _usfm_clean(t)) for b, c, v, t in rows if _usfm_clean(t)]
    yield from _group_verses(rows, title)


def parse_tanzil_xml(path: Path, title: str) -> Iterator[tuple[str, str]]:
    """Tanzil XML: <sura index name><aya index text/></sura>. Адрес: «Коран (Крачковский): 2:255»."""
    import xml.etree.ElementTree as ET
    raw = re.sub(rb"<!--.*?-->", b"", path.read_bytes(), flags=re.S)   # в комментариях Tanzil бывают «--»
    root = ET.fromstring(raw)
    rows = []
    for s in root.iter("sura"):
        name = s.get("name") or ""
        for a in s.iter("aya"):
            rows.append((f"сура {s.get('index')}" + (f" «{name}»" if name else ""), int(s.get("index")),
                         int(a.get("index")), a.get("text", "")))
    for ref, text in _group_verses(rows, title, group=3):
        yield re.sub(r"сура (\d+)( «[^»]*»)? \1:", r"\1:", ref), text


def parse_epub(path: Path, title: str) -> Iterator[tuple[str, str]]:
    import html as _html
    import zipfile
    with zipfile.ZipFile(path) as z:
        opf_name = next((n for n in z.namelist() if n.endswith(".opf")), None)
        files = []
        if opf_name:
            opf = z.read(opf_name).decode("utf-8", errors="ignore")
            base = opf_name.rsplit("/", 1)[0] + "/" if "/" in opf_name else ""
            manifest = dict(re.findall(r'<item[^>]+id="([^"]+)"[^>]+href="([^"]+)"', opf))
            manifest.update({i: h for h, i in re.findall(r'<item[^>]+href="([^"]+)"[^>]+id="([^"]+)"', opf)})
            files = [base + manifest[i] for i in re.findall(r'<itemref[^>]+idref="([^"]+)"', opf) if i in manifest]
        files = files or sorted(n for n in z.namelist() if n.endswith((".xhtml", ".html", ".htm")))
        for f in files:
            try:
                raw = z.read(f).decode("utf-8", errors="ignore")
            except KeyError:
                continue
            head = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", raw, re.S)
            sect = re.sub(r"<[^>]+>", "", head.group(1)).strip()[:80] if head else Path(f).stem
            text = _html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ",
                                                                  raw.replace("</p>", "\n\n"))))
            for i, chunk in enumerate(_split(re.sub(r"[ \t]+", " ", text)), 1):
                yield f"{title}: {sect}" + (f" ({i})" if i > 1 else ""), chunk


def parse_stardict(path: Path, title: str) -> Iterator[tuple[str, str]]:
    """FreeDict StarDict (.tar.xz с .ifo/.idx/.dict.dz) → статьи «слово — перевод»."""
    import gzip
    import struct
    import tarfile
    with tarfile.open(path) as t:
        files = {m.name.rsplit("/", 1)[-1]: t.extractfile(m).read() for m in t.getmembers() if m.isfile()}
    idx = next((v for k, v in files.items() if k.endswith(".idx")), None)
    if idx is None:
        idx = gzip.decompress(next(v for k, v in files.items() if k.endswith(".idx.gz")))
    dz = next((v for k, v in files.items() if k.endswith(".dict.dz")), None)
    data = gzip.decompress(dz) if dz else next(v for k, v in files.items() if k.endswith(".dict"))
    i = 0
    while i < len(idx):
        end = idx.index(b"\0", i)
        word = idx[i:end].decode("utf-8", errors="ignore")
        off, size = struct.unpack(">II", idx[end + 1:end + 9])
        i = end + 9
        d = re.sub(r"<[^>]+>", " ", data[off:off + size].decode("utf-8", errors="ignore"))
        yield f"{title}: {word}", f"{word} — {re.sub(r'\s+', ' ', d).strip()[:600]}"


def parse_pdf_zip(path: Path, title: str) -> Iterator[tuple[str, str]]:
    import tempfile
    import zipfile
    with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory() as td:
        for name in sorted(n for n in z.namelist() if n.lower().endswith(".pdf")):
            p = Path(td) / Path(name).name
            p.write_bytes(z.read(name))
            yield from parse_pdf(p, f"{title}, {Path(name).stem.split('_')[-1]}")


def parse_md_zip(path: Path, title: str) -> Iterator[tuple[str, str]]:
    import zipfile
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if n.lower().endswith(".md")):
            text = z.read(name).decode("utf-8", errors="ignore")
            text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
            sect = Path(name).stem.replace("-", " ")
            for i, chunk in enumerate(_split(text), 1):
                yield f"{title}: {sect}" + (f" ({i})" if i > 1 else ""), chunk


PARSERS = {"plain": parse_plain, "txt": parse_plain, "md": parse_plain, "pdf": parse_pdf,
           "verses_json": parse_verses, "tanzil": parse_tanzil, "usfm_zip": parse_usfm_zip,
           "tanzil_xml": parse_tanzil_xml, "epub": parse_epub, "stardict": parse_stardict,
           "pdf_zip": parse_pdf_zip, "md_zip": parse_md_zip}
NO_VECTORS = {"stardict"}                    # словари — точный поиск по слову, векторы не нужны


# ─── индексация ───────────────────────────────────────────────────────────

def _embed(texts: list[str]) -> np.ndarray:
    import ollama
    out = []
    client = ollama.Client(timeout=600)
    for i in range(0, len(texts), 64):
        r = client.embed(model=EMBED_MODEL, input=[t[:2000] for t in texts[i:i + 64]], keep_alive="60m")
        out.extend(r.embeddings)
    v = np.asarray(out, dtype=np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
    return v.astype(np.float16)


def index_item(it: dict, progress=None) -> int:
    """Проиндексировать элемент манифеста (текстовые форматы). Возвращает число фрагментов."""
    parser = PARSERS.get(it.get("parser") or it.get("format"))
    if not parser:
        return 0
    path = blob_path(it["sha256"])
    with _lock:
        old = [r[0] for r in _conn.execute("SELECT id FROM passages WHERE item=?", (it["id"],))]
        for pid in old:
            _conn.execute("INSERT INTO passages_fts(passages_fts, rowid, text, ref) "
                          "SELECT 'delete', id, text, ref FROM passages WHERE id=?", (pid,))
        _conn.execute("DELETE FROM passages WHERE item=?", (it["id"],))
        _conn.commit()
    # частями по 512 фрагментов: в памяти никогда не держим весь источник
    ids, vecs, batch, n = [], [], [], 0
    with_vectors = it.get("parser") not in NO_VECTORS

    def flush() -> None:
        with _lock:
            for ref, text in batch:
                cur = _conn.execute("INSERT INTO passages(item, ref, text, lang) VALUES (?,?,?,?)",
                                    (it["id"], ref, text, it.get("lang", "")))
                ids.append(cur.lastrowid)
                _conn.execute("INSERT INTO passages_fts(rowid, text, ref) VALUES (?,?,?)", (cur.lastrowid, text, ref))
            _conn.commit()
        if with_vectors:
            vecs.append(_embed([f"{r}\n{t}" for r, t in batch]))
        batch.clear()

    for row in parser(path, it.get("short") or it["title"]):
        batch.append(row)
        n += 1
        if len(batch) >= 512:
            flush()
            if progress:
                progress(n, 0)
    if batch:
        flush()
    with _lock:
        _conn.execute("INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?)",
                      (it["id"], it["title"], it["sha256"], it.get("lang", ""), it.get("tier", ""), n))
        _conn.commit()
    v = np.concatenate(vecs) if vecs else np.zeros((0, 1024), np.float16)
    np.save(VEC_DIR / f"{it['id']}.npy", v)
    np.save(VEC_DIR / f"{it['id']}.ids.npy", np.asarray(ids, dtype=np.int64))
    _vec_cache.pop(it["id"], None)
    return n


def indexed_items() -> list[tuple]:
    with _lock:
        return _conn.execute("SELECT id, title, lang, tier, n FROM items ORDER BY tier, id").fetchall()


# ─── поиск ────────────────────────────────────────────────────────────────

def _fts_query(q: str) -> str:
    words = [w for w in re.findall(r"\w+", q.lower()) if len(w) > 2]
    return " OR ".join(f'"{w[:max(4, len(w) - 2)]}"*' for w in words[:12])


def _vectors(item_id: str):
    if item_id not in _vec_cache:
        p = VEC_DIR / f"{item_id}.npy"
        if not p.exists():
            return None
        _vec_cache[item_id] = (np.load(p).astype(np.float32), np.load(VEC_DIR / f"{item_id}.ids.npy"))
    return _vec_cache[item_id]


def _dictionaries() -> set[str]:
    from .store import load_manifest
    return {i["id"] for i in load_manifest()["items"] if i.get("parser") == "stardict"}


def search(query: str, k: int = 6, items: list[str] | None = None, extra_query: str = "") -> list[dict]:
    """extra_query — тот же вопрос на другом языке (половина практической библиотеки англоязычная)."""
    with _lock:
        all_items = [r[0] for r in _conn.execute("SELECT id FROM items")]
    words = re.findall(r"\w+", query)
    dicts = _dictionaries()
    # словари — только для запросов из 1–2 слов или если их попросили явно
    scope = [i for i in all_items if (not items or i in items) and (i not in dicts or len(words) <= 2 or items)]
    if not scope:
        return []
    ranks: dict[int, float] = {}
    if len(words) <= 2 and set(scope) & dicts:                     # точное слово в словаре — первым
        ph = ",".join("?" * len(set(scope) & dicts))
        with _lock:
            exact = _conn.execute(f"SELECT id FROM passages WHERE item IN ({ph}) AND lower(ref) LIKE ?",
                                  (*(set(scope) & dicts), f"%: {query.strip().lower()}")).fetchall()
        for (pid,) in exact:
            ranks[pid] = 1.0
    if extra_query:
        for r, h in enumerate(search(extra_query, k=k * 2, items=scope)):
            ranks[h["_id"]] = ranks.get(h["_id"], 0) + 1 / (60 + r)
    fq = _fts_query(query)
    if fq:
        ph = ",".join("?" * len(scope))
        with _lock:
            try:
                rows = _conn.execute(f"SELECT p.id FROM passages_fts f JOIN passages p ON p.id=f.rowid "
                                     f"WHERE passages_fts MATCH ? AND p.item IN ({ph}) ORDER BY bm25(passages_fts) LIMIT 40",
                                     (fq, *scope)).fetchall()
            except sqlite3.OperationalError:
                rows = []
        for r, (pid,) in enumerate(rows):
            ranks[pid] = ranks.get(pid, 0) + 1 / (60 + r)
    try:
        qv = _embed([query])[0].astype(np.float32)
    except Exception:                       # нет модели эмбеддингов в Ollama — ищем только по словам (FTS5)
        qv = None
    scored = []
    for it in (scope if qv is not None else []):
        v = _vectors(it)
        if v is None or not len(v[0]):
            continue
        s = v[0] @ qv
        top = np.argsort(-s)[:40]
        scored.extend((float(s[t]), int(v[1][t])) for t in top)
    scored.sort(reverse=True)
    for r, (_, pid) in enumerate(scored[:60]):
        ranks[pid] = ranks.get(pid, 0) + 1 / (60 + r)
    best = sorted(ranks, key=ranks.get, reverse=True)[:k]
    if not best:
        return []
    with _lock:
        rows = {r[0]: r for r in _conn.execute(
            f"SELECT p.id, p.item, p.ref, p.text, i.title FROM passages p JOIN items i ON i.id=p.item "
            f"WHERE p.id IN ({','.join('?' * len(best))})", best)}
    return [{"item": rows[b][1], "ref": rows[b][2], "text": rows[b][3], "title": rows[b][4], "_id": b}
            for b in best if b in rows]
