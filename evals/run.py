"""Прогон экзамена: python evals/run.py [--model gemma4:e4b] [--only id1,id2] [--repeat N]

Работает в песочнице: известные папки и данные Jarvis подменяются временными, действия с внешним миром
(браузер, звук, буфер, shell, облако) только записываются.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = Path(tempfile.gettempdir()) / "jarvis_eval_sandbox"
DATA = Path(tempfile.gettempdir()) / "jarvis_eval_data"

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="")
ap.add_argument("--only", default="")
ap.add_argument("--repeat", type=int, default=1)
ap.add_argument("--quiet", action="store_true")
args = ap.parse_args()

os.environ["JARVIS_SANDBOX"] = str(SANDBOX)
os.environ["JARVIS_DATA_DIR"] = str(DATA)
shutil.rmtree(DATA, ignore_errors=True)
DATA.mkdir(parents=True)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from jarvis import bus, builtin_tools, coder, journal, library, llm, memory, power_tools, selfupdate, sorting, tools  # noqa: E402
from jarvis.agent import Agent  # noqa: E402
from jarvis.config import settings  # noqa: E402
from cases import CASES  # noqa: E402

if args.model:
    settings.model = args.model
settings.home_city = "Лос-Анджелес (US)"        # домашний город песочницы (погода в сводке)
llm.ensure_ollama()
selfupdate.load_all_skills()

# ─── внешний мир — только запись ──────────────────────────────────────────
_MOCK = {
    "open_thing": "Открываю {target}", "media_key": "{action} ×1", "lock_screen": "Экран заблокирован.",
    "run_powershell": "exit=0", "clipboard_write": "Скопировано в буфер обмена.", "clipboard_read": "<clipboard>пусто</clipboard>",
    "learn_skill": "Программист недоступен (экзамен).", "improve_self": "Недоступно (экзамен).",
    "create_skill": "Недоступно (экзамен).", "edit_own_code": "Недоступно (экзамен).",
    "restart_self": "Ок.",
    "look_at_screen": "<screen trust='untrusted'>Открыт браузер Chrome, на экране YouTube: видео «Трейлер GTA 6», пауза на 1:12.</screen>",
    "save_screenshot": "Скриншот сохранён.", "focus_window": "Переключил на: {name}", "close_app": "Закрыл: {name}",
    "list_windows": "chrome.exe — YouTube - Google Chrome\nTelegram.exe — Telegram\nCode.exe — jarvis - Visual Studio Code\n(Это только заголовки. Что видно внутри окна — какое видео, текст, ошибка — узнай через look_at_screen.)",
    "set_volume": "Громкость {percent}%.", "get_volume": "Громкость 40%.", "set_brightness": "Яркость {percent}%.",
    "youtube_play": "Включаю на YouTube: {query}", "rollback_self": "Недоступно (экзамен).", "delete_skill": "Недоступно (экзамен).",
}
for name, tpl in _MOCK.items():
    if name in tools.REGISTRY:
        tools.REGISTRY[name].func = (lambda tpl: lambda **kw: tpl.format_map({**{k: "" for k in ("target", "action", "name", "percent", "query")}, **kw}))(tpl)

calls: list[dict] = []
bus.subscribe(lambda e: calls.append({"name": e["tool"], "args": e["args"]}) if e["type"] == "tool_start" else None)


def setup(files: dict) -> None:
    shutil.rmtree(SANDBOX, ignore_errors=True)
    for d in ("Desktop", "Downloads", "Documents", "Pictures", "Music", "Videos"):
        (SANDBOX / d).mkdir(parents=True)
    for rel, content in (files or {}).items():
        p = SANDBOX / rel
        if content is None:
            p.parent.mkdir(parents=True, exist_ok=True)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, tuple) and content[0] == "docx":
            import docx
            d = docx.Document()
            d.add_paragraph(content[1])
            d.save(str(p))
            continue
        p.write_text(content, encoding="utf-8")
    for f in ("journal.jsonl", "reminders.json", "notes.json"):
        (DATA / f).unlink(missing_ok=True)
    with memory._lock:
        memory._conn.execute("DELETE FROM facts")
        memory._conn.execute("DELETE FROM messages")
        memory._conn.commit()
    sorting._last_plan = None


def run_case(case: dict) -> dict:
    setup(case.get("files"))
    agent = Agent()
    agent._ask = lambda t, a, s: True          # подтверждения — «да» (проверяем, ЧТО он решил сделать)
    calls.clear()
    answers, t0 = [], time.time()
    for turn in case["turns"]:
        if turn == "@reset":
            agent.reset()
            continue
        answers.append(agent.run(turn, source="voice"))
    ctx = {"calls": list(calls), "answers": answers, "fs": lambda p: SANDBOX / p,
           "exists": lambda p: (SANDBOX / p).exists()}
    try:
        problems = case["check"](ctx)
    except Exception as e:
        problems = [f"проверка упала: {e!r}"]
    return {"id": case["id"], "ok": not problems, "problems": problems, "secs": round(time.time() - t0, 1),
            "calls": [f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)[:120]})" for c in ctx["calls"]],
            "answers": answers}


def main() -> None:
    only = set(filter(None, args.only.split(",")))
    cases = [c for c in CASES if not only or c["id"] in only]
    results = []
    for rep in range(args.repeat):
        for c in cases:
            r = run_case(c)
            results.append(r)
            mark = "✅" if r["ok"] else "❌"
            print(f"{mark} {r['id']:<14} {r['secs']:>5}s  {' → '.join(x.split('(')[0] for x in r['calls']) or '—'}", flush=True)
            if not r["ok"] and not args.quiet:
                for p in r["problems"]:
                    print(f"     · {p}")
                print(f"     ответ: {r['answers'][-1][:200]!r}")
    passed = sum(r["ok"] for r in results)
    print(f"\nИТОГ {settings.model}: {passed}/{len(results)} ({100 * passed // max(1, len(results))}%), "
          f"среднее {sum(r['secs'] for r in results) / max(1, len(results)):.1f} с на случай")
    out = ROOT / "evals" / "results"
    out.mkdir(exist_ok=True)
    (out / f"{settings.model.replace(':', '_')}_{time.strftime('%m%d_%H%M')}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
