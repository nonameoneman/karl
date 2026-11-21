"""Шина событий: агент, голос и напоминания публикуют события, веб-UI и TTS их получают."""
from __future__ import annotations

import threading
from typing import Callable

_subs: list[Callable[[dict], None]] = []
_lock = threading.Lock()


def subscribe(fn: Callable[[dict], None]) -> None:
    with _lock:
        _subs.append(fn)


def publish(event: dict) -> None:
    with _lock:
        subs = list(_subs)
    for fn in subs:
        try:
            fn(event)
        except Exception:
            pass


def say(text: str) -> None:
    """Сказать вслух (если голос включён) и показать в UI."""
    publish({"type": "say", "text": text})
