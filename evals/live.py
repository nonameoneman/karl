"""Живая проверка на настоящей системе через API работающего Russel (http://127.0.0.1:8765).

Только безопасные или обратимые действия; каждое изменение откатывается и проверяется.
Запуск: python evals/live.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import websockets

API = "http://127.0.0.1:8765"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from russel.builtin_tools import known_folder  # noqa: E402

DESK = known_folder("desktop")


async def ask(text: str, approve: bool = True, timeout: float = 240) -> dict:
    calls, answer, t0 = [], None, time.time()
    async with websockets.connect(API.replace("http", "ws") + "/ws", max_size=None) as ws:
        r = httpx.post(API + "/api/chat", json={"text": text}, timeout=10).json()
        if not r.get("ok"):
            return {"answer": f"ОШИБКА: {r}", "calls": [], "secs": 0}
        while time.time() - t0 < timeout:
            try:
                e = json.loads(await asyncio.wait_for(ws.recv(), 5))
            except asyncio.TimeoutError:
                continue
            if e["type"] == "tool_start":
                calls.append((e["tool"], e["args"]))
            elif e["type"] == "confirm":
                httpx.post(API + "/api/confirm", json={"id": e["id"], "approved": approve})
            elif e["type"] == "final":
                answer = e["text"]
                break
    return {"answer": answer, "calls": calls, "secs": round(time.time() - t0, 1)}


def show(q: str, r: dict, verdict: str = "") -> None:
    tl = " → ".join(f"{n}({json.dumps(a, ensure_ascii=False)[:70]})" for n, a in r["calls"]) or "—"
    print(f"\n▶ {q}\n  {r['secs']}s  {tl}\n  «{r['answer']}»" + (f"\n  {verdict}" if verdict else ""), flush=True)


def snapshot(folder: Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*")} if folder.exists() else set()


async def main() -> None:
    from pycaw.pycaw import AudioUtilities
    ep = AudioUtilities.GetSpeakers().EndpointVolume
    vol0 = ep.GetMasterVolumeLevelScalar()
    httpx.post(API + "/api/reset")

    # — чтение —
    for q in ["Какая сейчас громкость?", "Сколько свободно на диске C?", "Что у меня сейчас на экране?",
              "Какие окна открыты?", "Какая погода в Лос-Анджелесе завтра?", "Сколько будет 18 процентов от 4 200 000?",
              "Какой самый большой файл в загрузках?", "Найди файл реестр сборки", "Курс евро к сому?"]:
        show(q, await ask(q))

    # — громкость с восстановлением —
    r = await ask("Поставь громкость на 15")
    now = round(ep.GetMasterVolumeLevelScalar() * 100)
    show("Поставь громкость на 15", r, f"{'✅' if now == 15 else '❌'} факт: {now}%")
    ep.SetMasterVolumeLevelScalar(vol0, None)
    print(f"  ↩ громкость возвращена на {round(vol0 * 100)}%")

    # — папка + откат —
    before = snapshot(DESK)
    r = await ask("Создай на рабочем столе папку Russel тест")
    show("Создай на рабочем столе папку Russel тест", r, f"{'✅' if (DESK / 'Russel тест').is_dir() else '❌'} папка есть")
    r = await ask("Верни как было")
    show("Верни как было", r, f"{'✅' if snapshot(DESK) == before else '❌'} рабочий стол как до теста")

    # — проект Skynet: план → да → откат —
    httpx.post(API + "/api/reset")
    before = snapshot(DESK)
    show("Собери файлы проекта Skynet на рабочем столе", await ask("Собери файлы проекта Skynet на рабочем столе"),
         f"{'✅' if snapshot(DESK) == before else '❌'} до «да» ничего не сдвинулось")
    r = await ask("Да")
    moved = sorted(p.name for p in (DESK / "Skynet").glob("*")) if (DESK / "Skynet").exists() else []
    others_ok = {x for x in before if not x.lower().startswith("skynet")} <= snapshot(DESK)
    show("Да", r, f"{'✅' if len(moved) == 4 and others_ok else '❌'} в Skynet: {moved}; остальное на месте: {others_ok}")
    r = await ask("Верни как было")
    show("Верни как было", r, f"{'✅' if snapshot(DESK) == before else '❌'} рабочий стол как до теста")

    # — дела —
    httpx.post(API + "/api/reset")
    for q in ["Добавь в список дел проверить Рассела", "Что у меня в списке дел?", "Отметь, что проверил Рассела"]:
        show(q, await ask(q))


asyncio.run(main())
