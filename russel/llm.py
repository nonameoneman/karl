"""Связь с локальной моделью через Ollama (запускает сервер сам, если он не запущен)."""
from __future__ import annotations

import os
import subprocess
import time
from typing import Iterator

import httpx
import ollama

from .config import OLLAMA_EXE, OLLAMA_MODELS, settings

_client = ollama.Client(host="http://127.0.0.1:11434", timeout=600)


def ollama_alive() -> bool:
    try:
        return httpx.get("http://127.0.0.1:11434/api/version", timeout=1.5).status_code == 200
    except Exception:
        return False


def ensure_ollama() -> None:
    if ollama_alive():
        return
    env = dict(os.environ, OLLAMA_HOST="127.0.0.1:11434", OLLAMA_KEEP_ALIVE="30m", OLLAMA_FLASH_ATTENTION="1")
    if OLLAMA_MODELS:
        env["OLLAMA_MODELS"] = OLLAMA_MODELS
    subprocess.Popen([str(OLLAMA_EXE), "serve"], env=env, creationflags=0x08000000,  # CREATE_NO_WINDOW
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        if ollama_alive():
            return
        time.sleep(0.5)
    raise RuntimeError("Ollama не запустилась")


def warmup() -> None:
    _client.chat(model=settings.model, messages=[{"role": "user", "content": "ok"}],
                 options={"num_ctx": settings.num_ctx, "num_predict": 1}, keep_alive="30m", think=False)


def chat_stream(messages: list[dict], tools: list[dict] | None = None) -> Iterator[tuple[str, object]]:
    calls: list[dict] = []
    stream = _client.chat(model=settings.model, messages=messages, tools=tools or None, stream=True,
                          think=settings.think, keep_alive="30m",
                          options={"num_ctx": settings.num_ctx, "temperature": settings.temperature})
    for chunk in stream:
        m = chunk.message
        if m.content:
            yield "token", m.content
        if m.tool_calls:
            for tc in m.tool_calls:
                calls.append({"name": tc.function.name, "arguments": dict(tc.function.arguments or {})})
    if calls:
        yield "tool_calls", calls


def complete(prompt: str, system: str = "") -> str:
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    r = _client.chat(model=settings.model, messages=msgs, think=False, keep_alive="30m",
                     options={"num_ctx": settings.num_ctx, "temperature": 0.3})
    return r.message.content
