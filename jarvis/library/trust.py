"""Проверка целостности файлов библиотеки: хэши (sha256 своего хранилища, md5/sha1/sha256 первоисточника)."""
from __future__ import annotations

import hashlib
from pathlib import Path


def hash_file(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with path.open("rb") as f:
        while b := f.read(4 << 20):
            h.update(b)
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    return hash_file(path, "sha256")
