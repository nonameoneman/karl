"""Хранилище библиотеки: файлы по хэшу (content-addressed), текущий манифест, загрузка с проверкой.

<папка библиотеки>\\   — data/paths.json → library, по умолчанию ~/RusselLibrary
  blobs\\ab\\ab12…ef          — файлы по sha256 (один и тот же файл у всех узлов — один и тот же адрес)
  manifest.json             — принятый манифест (что есть в библиотеке)
  partial\\                  — недокачанное (докачка после обрыва)
  index\\library.db          — поиск: FTS5 + векторы
  node_key.pem              — ключ узла (подписывает подтверждения для других узлов)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from .trust import hash_file, sha256_file

from ..config import LIBRARY_DIR as LIB_DIR
BLOBS = LIB_DIR / "blobs"
PARTIAL = LIB_DIR / "partial"
MANIFEST = LIB_DIR / "manifest.json"
for _d in (BLOBS, PARTIAL, LIB_DIR / "index"):
    _d.mkdir(parents=True, exist_ok=True)


def blob_path(sha: str) -> Path:
    return BLOBS / sha[:2] / sha


def has_blob(sha: str) -> bool:
    return bool(sha) and blob_path(sha).exists()


def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 0, "items": [], "signatures": []}


def save_manifest(m: dict) -> None:
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(MANIFEST)


def item(item_id: str) -> dict | None:
    return next((i for i in load_manifest()["items"] if i["id"] == item_id), None)


def ingest(path: Path, move: bool = False) -> tuple[str, int]:
    """Положить файл в хранилище; вернуть (sha256, размер)."""
    sha = sha256_file(path)
    dst = blob_path(sha)
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        (shutil.move if move else shutil.copy2)(str(path), str(dst))
    elif move:
        path.unlink(missing_ok=True)
    return sha, dst.stat().st_size


def _upstream_checksum(url: str | None) -> tuple[str, str] | None:
    """Kiwix публикует .sha256/.md5 рядом с файлом, Geofabrik — .md5. Возвращает (алгоритм, хэш)."""
    if not url:
        return None
    import httpx
    try:
        txt = httpx.get(url, timeout=30, follow_redirects=True).text.strip()
    except Exception:
        return None
    m = re.search(r"\b([0-9a-f]{64}|[0-9a-f]{40}|[0-9a-f]{32})\b", txt.lower())
    if not m:
        return None
    h = m.group(1)
    return {64: "sha256", 40: "sha1", 32: "md5"}[len(h)], h


UA = "RusselOfflineLibrary/1.0 (personal offline archive)"


def _ua(url: str) -> str:
    """Wikimedia требует контакт в User-Agent; он берётся из локальных настроек и уходит только к ним."""
    if "wikimedia.org" in url or "wikipedia.org" in url:
        try:
            from ..config import settings
            if getattr(settings, "contact_email", ""):
                return f"RusselOfflineLibrary/1.0 (personal offline archive; {settings.contact_email})"
        except Exception:
            pass
    return UA


def _curl(url: str, dst: Path, retries: int = 5) -> None:
    """Запасной путь: некоторые серверы (Wikimedia) отклоняют python-клиенты или ограничивают частоту."""
    import subprocess
    for i in range(retries):
        r = subprocess.run(["curl", "-sSL", "--fail", "-C", "-", "-A", _ua(url), "-o", str(dst), url],
                           capture_output=True, text=True, timeout=7200)
        if r.returncode == 0:
            return
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"curl не скачал {url}: {r.stderr.strip()[:200]}")


def download_entry(entry: dict, progress=None) -> tuple[str, int, str]:
    """Скачать элемент каталога любого вида: один файл, список файлов (→ zip) или git-репозиторий (→ zip)."""
    import zipfile
    name = entry["id"]
    if entry.get("git"):
        import subprocess
        dst = PARTIAL / f"{name}_git"
        shutil.rmtree(dst, ignore_errors=True)
        subprocess.run(["git", "clone", "--depth", "1", "-q", entry["git"], str(dst)], check=True, timeout=1800)
        shutil.rmtree(dst / ".git", ignore_errors=True)
        z = PARTIAL / f"{name}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(dst.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(dst).as_posix())
        shutil.rmtree(dst, ignore_errors=True)
        sha, size = ingest(z, move=True)
        return sha, size, "git-клон первоисточника"
    if entry.get("urls"):
        z = PARTIAL / f"{name}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:
            for k, u in enumerate(entry["urls"], 1):
                part = PARTIAL / f"{name}_{k}{Path(u).suffix}"
                _fetch(u, part)
                zf.write(part, Path(u).name)
                part.unlink(missing_ok=True)
                if progress:
                    progress(k, len(entry["urls"]))
        sha, size = ingest(z, move=True)
        return sha, size, f"{len(entry['urls'])} файлов первоисточника"
    return download(entry["url"], name + Path(entry["url"].split("?")[0]).suffix, progress,
                    checksum_url=entry.get("checksum_url"), checksum=entry.get("checksum"))


def _fetch(url: str, part: Path, progress=None) -> None:
    import httpx
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": _ua(url)}
    if have:
        headers["Range"] = f"bytes={have}-"
    try:
        with httpx.stream("GET", url, headers=headers, timeout=60, follow_redirects=True) as r:
            if r.status_code in (403, 429):
                raise PermissionError(r.status_code)
            if r.status_code == 416:
                return                                # уже скачано целиком
            if r.status_code == 200 and have:
                have = 0
            elif r.status_code not in (200, 206):
                raise RuntimeError(f"HTTP {r.status_code} для {url}")
            total = have + int(r.headers.get("content-length") or 0)
            t0, done = time.time(), have
            with part.open("ab" if have else "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if progress and time.time() - t0 > 1:
                        progress(done, total)
                        t0 = time.time()
    except PermissionError:
        _curl(url, part)


def download(url: str, name: str, progress=None, checksum_url: str | None = None,
             expect_sha256: str | None = None, checksum: str | None = None) -> tuple[str, int, str]:
    """Скачать с докачкой; проверить хэш первоисточника (если он публикует) и/или ожидаемый sha256.

    Возвращает (sha256, размер, как_проверено). Исключение — если проверка не прошла.
    """
    part = PARTIAL / re.sub(r"[^\w.\-]+", "_", name)
    _fetch(url, part, progress)
    how = []
    if checksum and ":" in checksum:                  # контрольная сумма, известная заранее (Commons SHA-1, FreeDict SHA-512)
        algo, want = checksum.split(":", 1)
        got = hash_file(part, algo)
        if got.lower() != want.lower():
            part.unlink(missing_ok=True)
            raise RuntimeError(f"хэш не совпал с первоисточником ({algo}): {got[:16]}… ≠ {want[:16]}…")
        how.append(f"{algo} первоисточника совпал")
    up = _upstream_checksum(checksum_url)
    if up:
        algo, want = up
        got = hash_file(part, algo)
        if got != want:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"хэш не совпал с первоисточником ({algo}): {got} ≠ {want}")
        how.append(f"{algo} первоисточника совпал")
    sha, size = ingest(part, move=True)
    if expect_sha256 and sha != expect_sha256:
        blob_path(sha).unlink(missing_ok=True)
        raise RuntimeError(f"sha256 не совпал с манифестом: {sha} ≠ {expect_sha256}")
    if expect_sha256:
        how.append("sha256 манифеста совпал")
    return sha, size, ", ".join(how) or "без внешней проверки"
