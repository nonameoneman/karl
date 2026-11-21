"""Веб-сервер Russel: UI на http://127.0.0.1:8765, WebSocket с событиями, REST для действий."""
from __future__ import annotations

import asyncio
import json
import re
import threading
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import builtin_tools, bus, coder, journal, library, memory, power_tools, profile, selfupdate, sorting, tools  # noqa: F401  (регистрирует инструменты)
from .agent import agent
from .config import DATA_DIR, PKG, settings

app = FastAPI(title="Russel")
_clients: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None
_log: list[dict] = []                     # последние события для новых вкладок

speaker = None
listener = None


_EPHEMERAL = ("token", "mic_level", "audio", "audio_stop", "thinking")
_audio_clients = 0                        # вкладки, которые шлют звук и играют ответы
_audio_stack: list[int] = []              # порядок подключения; активна последняя
_audio_seq = 0


def _on_event(ev: dict) -> None:
    if ev.get("type") == "audio" and _audio_stack:
        ev = {**ev, "to": _audio_stack[-1]}      # звук играет только одно окно — иначе два окна дают эхо
    if ev.get("type") not in _EPHEMERAL:
        _log.append(ev)
        del _log[:-300]
    if _loop:
        for q in list(_clients):
            _loop.call_soon_threadsafe(q.put_nowait, ev)


bus.subscribe(_on_event)


_vt = {"turn": None, "buf": "", "spoke": False}   # озвучка текущего голосового хода по предложениям
_SENT = re.compile(r"^(.{12,}?[.!?…;:\n])(\s+|$)", re.S)


def _flush_sentences(final: bool = False) -> None:
    while True:
        m = _SENT.match(_vt["buf"])
        if not m:
            break
        speaker.say(m.group(1))
        _vt["spoke"] = True
        _vt["buf"] = _vt["buf"][m.end():]
    if final and _vt["buf"].strip():
        speaker.say(_vt["buf"])
        _vt["spoke"] = True
        _vt["buf"] = ""


def _voice_events(ev: dict) -> None:
    if not speaker:
        return
    t = ev.get("type")
    if t == "user":
        if ev.get("source") == "voice":
            speaker.stop()
            _vt.update(turn=ev.get("turn"), buf="", spoke=False)
        else:
            _vt["turn"] = None
    elif t == "token" and ev.get("turn") == _vt["turn"] and settings.voice_enabled:
        _vt["buf"] += ev["text"]
        _flush_sentences()
    elif t == "tool_start" and ev.get("turn") == _vt["turn"]:
        _flush_sentences(final=True)
    elif t == "say" and settings.voice_enabled:
        speaker.say(ev["text"])
    elif t == "final" and ev.get("source") == "voice":
        if settings.voice_enabled:
            _flush_sentences(final=True)
            if not _vt["spoke"]:
                speaker.say(ev["text"])
        _vt["turn"] = None
        if listener:
            listener.open_followup()
    elif t == "confirm" and ev.get("source") == "voice":
        if ev["risk"] == "yellow":
            speaker.say(f"Выполнить {ev['tool'].replace('_', ' ')}? Скажи да или нет.")
        else:
            speaker.say("Это опасное действие. Подтверди на экране.")


bus.subscribe(_voice_events)


def run_command(text: str, source: str = "text") -> None:
    threading.Thread(target=agent.run, args=(text, source), daemon=True).start()


# ─── HTTP ──────────────────────────────────────────────────────────────────

class ChatIn(BaseModel):
    text: str


class ConfirmIn(BaseModel):
    id: str
    approved: bool
    always: bool = False


@app.get("/")
def index():
    return FileResponse(PKG / "web" / "app.html")


@app.get("/i18n.js")
def i18n():
    return FileResponse(PKG / "web" / "i18n.js", media_type="application/javascript")


@app.post("/api/client_log")
def client_log(body: dict):
    """Ошибки окна (микрофон и т.п.) — в лог ядра, чтобы их было видно при разборе."""
    import time as _t
    with (DATA_DIR / "client.log").open("a", encoding="utf-8") as f:
        f.write(f"{_t.strftime('%Y-%m-%d %H:%M:%S')} {str(body.get('text', ''))[:300]}\n")
    return {"ok": True}


@app.post("/api/greet")
def greet():
    """Приветствие при знакомстве — голосом, если озвучка включена."""
    from . import lang
    if speaker and settings.voice_enabled:
        speaker.say(lang.greeting(settings.user_name))
    return {"ok": True}


@app.post("/api/lang")
def set_lang(body: dict):
    """Сменить язык интерфейса и разговора (голос и распознавание переключаются сами)."""
    from . import lang
    code = str(body.get("lang", ""))
    if code not in lang.LANGS:
        return JSONResponse({"error": "нет такого языка"}, status_code=400)
    settings.language = code
    settings.save()
    return {"ok": True, "lang": code}


@app.get("/api/profile")
def profile_view():
    return profile.view()


@app.get("/api/profile/next")
def profile_next(force: int = 0):
    """Следующий вопрос анкеты (или null): окно спрашивает только когда разговор закончен."""
    return {"q": None if agent.busy else profile.next_question(bool(force))}


@app.post("/api/profile/answer")
def profile_answer(body: dict):
    ok = profile.answer(str(body.get("kind", "")), str(body.get("id", "")), body.get("value"),
                        bool(body.get("edit")))
    return {"ok": ok, "profile": profile.view()}


@app.post("/api/profile/goal_remove")
def profile_goal_remove(body: dict):
    profile.remove_goal_index(int(body.get("i", -1)))
    return profile.view()


@app.post("/api/profile/forget")
def profile_forget():
    profile.forget()
    return profile.view()


@app.get("/know.js")
def know_js():
    return FileResponse(PKG / "web" / "know.js", media_type="application/javascript")


@app.get("/maps/{name}")
def map_file(name: str):
    """Файлы офлайн-карты (PMTiles) — с поддержкой Range, карта читает их кусками."""
    from .library.store import LIB_DIR
    p = (LIB_DIR / "maps" / name)
    if not re.fullmatch(r"[\w.-]+\.pmtiles", name) or not p.exists():
        return JSONResponse({"error": "нет такой карты"}, status_code=404)
    return FileResponse(p, media_type="application/octet-stream")


@app.get("/api/maps")
def maps_list():
    from .library.store import LIB_DIR
    return {"maps": sorted(p.name for p in (LIB_DIR / "maps").glob("*.pmtiles"))}


@app.get("/pro")
def pro():
    return FileResponse(PKG / "web" / "index.html")


@app.post("/api/chat")
def chat(body: ChatIn):
    if agent.busy:
        return JSONResponse({"ok": False, "error": "Я ещё занят предыдущей задачей"}, status_code=409)
    run_command(body.text.strip(), "text")
    return {"ok": True}


@app.post("/api/confirm")
def confirm(body: ConfirmIn):
    return {"ok": agent.resolve_confirm(body.id, body.approved, body.always)}


@app.post("/api/stop")
def stop():
    agent.stop_flag.set()
    for p in list(agent._pending.values()):
        agent.resolve_confirm(p["id"], False)
    return {"ok": True}


@app.post("/api/reset")
def reset():
    agent.reset()
    return {"ok": True}


@app.post("/api/undo")
def undo():
    return {"ok": True, "report": journal.undo_turn()}


@app.post("/api/voice/{action}")
def voice(action: str):
    if not listener:
        return {"ok": False, "error": "голос не инициализирован"}
    if action == "on":
        listener.start("system")
    elif action == "browser":
        listener.start("browser")
    elif action == "off":
        listener.stop()
    elif action == "mute_tts":
        settings.voice_enabled = not settings.voice_enabled
        settings.save()
    return {"ok": True, "state": listener.state, "tts": settings.voice_enabled}


@app.post("/api/shutup")
def shutup():
    if speaker:
        speaker.stop()
    return {"ok": True}


class VoiceIn(BaseModel):
    voice: str = ""
    speed: float = 0


@app.post("/api/tts")
def tts_settings(body: VoiceIn):
    if body.voice in ("dmitri", "ruslan", "denis", "irina"):
        settings.tts_voice = body.voice
    if 0.5 <= body.speed <= 1.5:
        settings.tts_speed = body.speed
    settings.save()
    if speaker:
        speaker.stop()
        speaker.say("Так я звучу теперь.")
    return {"ok": True, "voice": settings.tts_voice, "speed": settings.tts_speed}


@app.post("/api/restart")
def restart():
    threading.Timer(0.5, selfupdate.do_restart).start()
    return {"ok": True}


@app.get("/api/state")
def state():
    skills: dict[str, list[str]] = {}
    for t in tools.REGISTRY.values():
        skills.setdefault(t.source, []).append(t.name)
    return {
        "model": settings.model,
        "busy": agent.busy,
        "voice": listener.state if listener else "off",
        "mic_source": listener.source if listener else None,
        "tts_voice": settings.tts_voice,
        "language": settings.language,
        "user_name": settings.user_name,
        "tts_speed": settings.tts_speed,
        "tts": settings.voice_enabled,
        "tools": [{"name": t.name, "risk": t.risk, "source": t.source, "description": t.description}
                  for t in tools.REGISTRY.values()],
        "facts": [{"id": i, "text": t} for i, t in memory.all_facts(100)],
        "history": journal.history(30),
        "git": selfupdate.log(15),
        "always_allow": settings.always_allow,
        "pending": {k: v for k, v in (agent.pending_confirm() or {}).items() if k != "event"} or None,
        "log": _log[-80:],
    }


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    q: asyncio.Queue = asyncio.Queue()
    _clients.add(q)
    try:
        while True:
            ev = await q.get()
            await sock.send_text(json.dumps(ev, ensure_ascii=False, default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _clients.discard(q)


@app.websocket("/ws/audio")
async def ws_audio(sock: WebSocket):
    """Звук из браузера: int16 PCM, 16 кГц, моно — кадрами по ~32 мс."""
    import numpy as np
    global _audio_clients, _audio_seq
    await sock.accept()
    _audio_clients += 1
    _audio_seq += 1
    me = _audio_seq
    _audio_stack.append(me)                 # слушаем и озвучиваем только последнее подключённое окно
    await sock.send_text(json.dumps({"type": "hello", "id": me}))
    if listener and (listener.source != "browser" or not listener.enabled):
        listener.start("browser")
    try:
        while True:
            data = await sock.receive_bytes()
            if listener and listener.source == "browser" and _audio_stack and _audio_stack[-1] is me:
                listener.feed(np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _audio_clients -= 1
        if me in _audio_stack:
            _audio_stack.remove(me)


from fastapi.staticfiles import StaticFiles  # noqa: E402
app.mount("/vendor", StaticFiles(directory=PKG / "web" / "vendor"), name="vendor")


@app.on_event("startup")
async def _startup():
    global _loop
    _loop = asyncio.get_running_loop()
