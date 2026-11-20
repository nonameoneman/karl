"""Голоса Piper для языков Карла: скачать с проверкой md5 из каталога rhasspy/piper-voices."""
from __future__ import annotations

import hashlib
import httpx

from .config import MODELS_DIR

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
# язык интерфейса → голос (мужской, под Карла)
VOICES = {
    "ru": "ru_RU-dmitri-medium", "en": "en_GB-alan-medium", "zh": "zh_CN-chaowen-medium",
    "es": "es_ES-davefx-medium", "ar": "ar_JO-kareem-medium", "fr": "fr_FR-tom-medium",
    "hi": "hi_IN-rohan-medium", "pt": "pt_BR-faber-medium",
}


def fetch_all(log=print) -> None:
    c = httpx.Client(timeout=120, follow_redirects=True)
    cat = c.get(BASE + "voices.json").json()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for lang, key in VOICES.items():
        files = cat[key]["files"]
        for rel, meta in files.items():
            if not rel.endswith((".onnx", ".onnx.json")):
                continue
            dst = MODELS_DIR / rel.rsplit("/", 1)[-1]
            if dst.exists() and hashlib.md5(dst.read_bytes()).hexdigest() == meta["md5_digest"]:
                continue
            data = c.get(BASE + rel).content
            if hashlib.md5(data).hexdigest() != meta["md5_digest"]:
                raise RuntimeError(f"md5 не совпал: {rel}")
            dst.write_bytes(data)
            log(f"{lang}: {dst.name} {len(data) / 1e6:.0f} МБ — md5 совпал")


if __name__ == "__main__":
    fetch_all()
