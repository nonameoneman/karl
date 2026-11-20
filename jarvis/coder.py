"""«Мозг-программист» для самообучения: Claude Code CLI в headless-режиме.

Пишет навыки и правит ядро Jarvis, когда локальной модели не хватает. Данные задачи и код Jarvis уходят в облако —
поэтому инструменты, которые его вызывают, требуют подтверждения.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

from . import bus, selfupdate
from .config import ROOT, SKILLS_DIR
from .tools import tool

PY = sys.executable.replace("\\", "/")
CHECK = f"{PY} -m jarvis.skillcheck"


def available() -> bool:
    return shutil.which("claude") is not None


def _run_claude(prompt: str, allowed: list[str], timeout: int = 900) -> str:
    cmd = ["claude", "-p", prompt, "--output-format", "text", "--allowedTools", *allowed]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=0x08000000)
    return (r.stdout or r.stderr).strip()[-3000:]


SKILL_BRIEF = """Ты пишешь навык для локального ассистента Jarvis (Python 3.13, Windows). Задача навыка:
{task}

Требования:
- Один файл skills/_staging/{name}.py. Посмотри пример устройства навыков в jarvis/tools.py (декоратор @tool) и
  jarvis/builtin_tools.py (SKILL_TEMPLATE).
- Функции с @tool("green"|"yellow"|"red"), аннотации типов, docstring по-русски с разделом Args. Возвращают str (по-русски, коротко — ответ может быть озвучен).
- Сеть — только httpx (уже установлен), timeout=15, импорт внутри функции. Только бесплатные источники без ключей.
  Сначала найди источник (WebSearch) и посмотри его НАСТОЯЩИЙ ответ (WebFetch), потом разбирай именно его. Никаких заглушек и выдуманных данных.
- Обязательная функция test(): вызывает инструмент, проверяет результат через assert и возвращает его.
- Доступные пакеты: стандартная библиотека, httpx, psutil, numpy, ddgs. Другие не используй.
- Проверь навык командой: {check} skills/_staging/{name}.py — повторяй исправления, пока не будет OK.
- Ничего, кроме skills/_staging/{name}.py, не создавай и не меняй.
В конце одной строкой напиши, что умеет навык."""


@tool("yellow")
def learn_skill(task: str, name: str) -> str:
    """Научиться новому через внешнего программиста (Claude Code, облако): он исследует источник данных,
    пишет и проверяет навык, Jarvis подключает его сразу. Лучше create_skill для всего, что требует API или парсинга.

    Args:
        task: что должен уметь навык, подробно (какие данные, откуда, в каком виде ответ)
        name: имя навыка латиницей (currency_rates, weather)
    """
    if not available():
        return "Claude Code не установлен — используй create_skill."
    name = "".join(c if c.isalnum() or c == "_" else "_" for c in name.lower()).strip("_") or "skill"
    staging = SKILLS_DIR / "_staging"
    staging.mkdir(exist_ok=True)
    target = staging / f"{name}.py"
    final = SKILLS_DIR / f"{name}.py"
    if final.exists() and not target.exists():
        shutil.copy2(final, target)        # обновление существующего навыка
    bus.publish({"type": "status", "text": f"Учусь: {name} (Claude Code пишет навык, 1–5 минут)…"})
    try:
        summary = _run_claude(SKILL_BRIEF.format(task=task, name=name, check=CHECK),
                              ["Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch",
                               f"Bash({CHECK}:*)"])
    except subprocess.TimeoutExpired:
        return "Программист не уложился в 15 минут."
    if not target.exists():
        return f"Навык не создан. Ответ программиста: {summary[-800:]}"
    res = selfupdate.install_skill(name, target.read_text(encoding="utf-8"))
    return f"{res}\nПрограммист: {summary[-400:]}"


CORE_BRIEF = """Ты дорабатываешь собственный код локального ассистента Jarvis (этот репозиторий, Python 3.13, Windows).
Задача: {task}

Правила:
- Сначала прочитай ROADMAP.md и нужные файлы в jarvis/. Делай минимальные изменения, в стиле существующего кода.
- Не трогай data/ и .git. Не добавляй зависимости, которых нет в окружении.
- Проверь, что ядро импортируется: {py} -c "import jarvis.server"
- Не коммить — Jarvis закоммитит сам после проверки.
В конце двумя-тремя строками по-русски: что изменил и в каких файлах."""


@tool("red")
def improve_self(task: str) -> str:
    """Улучшить собственный код Jarvis через внешнего программиста (Claude Code, облако): новые возможности ядра,
    исправление ошибок, интерфейс. После правки — проверка импорта, коммит и перезапуск; сломанная правка откатывается.

    Args:
        task: что изменить или добавить, подробно
    """
    if not available():
        return "Claude Code не установлен — используй edit_own_code."
    before = selfupdate.git("rev-parse", "HEAD")
    selfupdate.commit("auto: перед improve_self")
    bus.publish({"type": "status", "text": "Улучшаю себя через Claude Code (до 15 минут)…"})
    try:
        summary = _run_claude(CORE_BRIEF.format(task=task, py=PY),
                              ["Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch",
                               f'Bash({PY} -c "import jarvis.server")'], timeout=900)
    except subprocess.TimeoutExpired:
        summary = "таймаут"
    changed = selfupdate.git("status", "--porcelain")
    if not changed:
        return f"Изменений нет. Программист: {summary[-800:]}"
    ok, out = selfupdate.check_core()
    if not ok:
        selfupdate.git("checkout", "--", ".")
        selfupdate.git("clean", "-fd", "jarvis", "skills")
        return f"Правка сломала ядро и откачена.\n{out[-800:]}"
    bus.publish({"type": "status", "text": "Правка собрана — сдаю экзамен (≈3 минуты)…"})
    score, total, report = selfupdate.run_evals()
    base = selfupdate.eval_baseline()
    if total and base and score < base - 1:          # 1 случай — допуск на случайность модели
        selfupdate.git("checkout", "--", ".")
        selfupdate.git("clean", "-fd", "jarvis", "skills")
        return f"Правка откачена: экзамен {score}/{total}, было {base}/{total}. Провалы:\n{report[-900:]}"
    if total:
        selfupdate.save_baseline(score)
    rev = selfupdate.commit(f"self: {task[:60]} (экзамен {score}/{total})")
    selfupdate.request_restart()
    return f"Готово, коммит {rev} (было {before[:7]}). Перезапущусь после ответа.\n{summary[-800:]}"
