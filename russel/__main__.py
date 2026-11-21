"""Запуск: python -m russel [--no-voice] [--no-browser]"""
from __future__ import annotations

import sys
import threading
import webbrowser

import uvicorn

from . import builtin_tools, bus, llm, selfupdate, server
from .config import settings


def main() -> None:
    args = set(sys.argv[1:])
    print("Russel: запускаю Ollama…", flush=True)
    llm.ensure_ollama()

    selfupdate.ensure_repo()
    loaded = selfupdate.load_all_skills()
    print(f"Russel: навыки {loaded}", flush=True)
    selfupdate.commit("auto: состояние при запуске")

    threading.Thread(target=builtin_tools.reminder_loop, daemon=True, name="reminders").start()

    def _warm():
        try:
            llm.warmup()
            bus.publish({"type": "status", "text": "Модель загружена"})
        except Exception as e:
            bus.publish({"type": "error", "text": f"Модель: {e}"})
    threading.Thread(target=_warm, daemon=True).start()

    if "--no-voice" not in args:
        try:
            from .voice import Listener, Speaker
            server.speaker = Speaker()
            server.speaker.browser_out = lambda: (server._audio_clients > 0 and server.listener is not None
                                                  and server.listener.source == "browser")
            server.listener = Listener(lambda cmd: server.run_command(cmd, "voice"), server.speaker)
            from .agent import agent
            server.listener.confirm_hook = (agent.pending_confirm, lambda cid, ok: agent.resolve_confirm(cid, ok))
            if settings.voice_autostart:
                server.listener.start()
        except Exception as e:
            print(f"Russel: голос недоступен: {e}", flush=True)

    url = f"http://{settings.host}:{settings.port}"
    print(f"Russel: {url}", flush=True)
    if "--no-browser" not in args:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(server.app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
