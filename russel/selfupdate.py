"""Самообновление: навыки (skills/*.py) и правки собственного кода с проверкой, git-коммитом и откатом."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

from . import tools
from .config import ROOT, SKILLS_DIR, DATA_DIR, RESTART_EXIT_CODE

PY = sys.executable
_restart_requested = threading.Event()


# ─── git ───────────────────────────────────────────────────────────────────

def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return (r.stdout + r.stderr).strip()


def ensure_repo() -> None:
    if not (ROOT / ".git").exists():
        git("init", "-q")
        git("config", "user.name", "Russel")
        git("config", "user.email", "russel@localhost")
    gi = ROOT / ".gitignore"
    if not gi.exists():
        gi.write_text("data/\n__pycache__/\n*.pyc\nskills/_staging/\n", encoding="utf-8")


def commit(msg: str) -> str:
    git("add", "-A")
    out = git("commit", "-q", "-m", msg)
    return git("rev-parse", "--short", "HEAD") if "nothing to commit" not in out else "(без изменений)"


def log(n: int = 10) -> str:
    return git("log", f"-{n}", "--pretty=format:%h %ad %s", "--date=format:%d.%m %H:%M")


# ─── Навыки ────────────────────────────────────────────────────────────────

def load_skill(path: Path) -> list[str]:
    src = f"skill:{path.stem}"
    tools.remove_source(src)
    tools.set_source(src)
    try:
        spec = importlib.util.spec_from_file_location(f"russel_skill_{path.stem}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        tools.set_source("core")
    return [t.name for t in tools.REGISTRY.values() if t.source == src]


def load_all_skills() -> dict[str, list[str] | str]:
    out: dict[str, list[str] | str] = {}
    for p in sorted(SKILLS_DIR.glob("*.py")):
        if p.name.startswith("_"):
            continue
        try:
            out[p.stem] = load_skill(p)
        except Exception as e:
            out[p.stem] = f"ошибка: {e}"
    return out


_VALIDATOR = textwrap.dedent("""
    import sys, json, importlib.util
    sys.path.insert(0, {root!r})
    from russel import tools
    tools.set_source("check")
    spec = importlib.util.spec_from_file_location("check_skill", {path!r})
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = [t.name for t in tools.REGISTRY.values() if t.source == "check"]
    if not names:
        raise SystemExit("в навыке нет ни одной функции с @tool")
    res = None
    if hasattr(mod, "test"):
        res = mod.test()
    print(json.dumps({{"tools": names, "test": repr(res)[:500]}}, ensure_ascii=False))
""")


def validate_skill_file(path: Path, timeout: int = 60) -> tuple[bool, str]:
    code = _VALIDATOR.format(root=str(ROOT), path=str(path))
    try:
        r = subprocess.run([PY, "-c", code], capture_output=True, text=True, encoding="utf-8",
                           timeout=timeout, cwd=str(DATA_DIR))
    except subprocess.TimeoutExpired:
        return False, f"проверка не уложилась в {timeout} с"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout)[-2500:]
    return True, r.stdout.strip()[-1500:]


_STUB_MARKERS = ("заглушк", "имитац", "примерный курс", "примерн", "mock", "placeholder", "todo", "fake",
                 "замените на реальн", "for example purposes", "dummy", "hardcode", "захардкож")


def _lint_skill(code: str) -> str | None:
    low = code.lower()
    hits = [m for m in _STUB_MARKERS if m in low]
    if hits:
        return (f"Похоже на заглушку (нашёл: {', '.join(hits)}). Навык обязан получать НАСТОЯЩИЕ данные. "
                "Сначала найди рабочий источник (web_search, затем fetch_url по адресу API и посмотри реальный ответ), "
                "потом напиши код, который разбирает именно этот ответ. Не используй выдуманные числа.")
    if "def test" not in code:
        return "Добавь функцию test(), которая вызывает инструмент и возвращает результат (по нему проверяю навык)."
    return None


def install_skill(name: str, code: str) -> str:
    name = "".join(c if c.isalnum() or c == "_" else "_" for c in name.lower()).strip("_") or "skill"
    problem = _lint_skill(code)
    if problem:
        return f"НЕ УСТАНОВЛЕН. {problem}"
    staging = SKILLS_DIR / "_staging"
    staging.mkdir(exist_ok=True)
    tmp = staging / f"{name}.py"
    tmp.write_text(code, encoding="utf-8")
    ok, out = validate_skill_file(tmp)
    if not ok:
        return f"НЕ УСТАНОВЛЕН. Проверка упала:\n{out}\nИсправь код и вызови create_skill снова."
    final = SKILLS_DIR / f"{name}.py"
    existed = final.exists()
    tmp.replace(final)
    names = load_skill(final)
    rev = commit(f"skill: {'update' if existed else 'add'} {name}")
    return (f"Навык {name} {'обновлён' if existed else 'установлен'} (коммит {rev}). Новые инструменты: {', '.join(names)}.\n"
            f"Вывод test(): {out}\nПроверь, что это настоящие данные, а не ошибка; если нет — исправь навык тем же create_skill.")


# ─── Правка собственного кода ───────────────────────────────────────────────

def check_core() -> tuple[bool, str]:
    r = subprocess.run([PY, "-c", "import russel.server; print('ok')"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    return r.returncode == 0, (r.stderr or r.stdout)[-2500:]


def edit_core(rel_path: str, old: str, new: str, reason: str) -> str:
    path = (ROOT / rel_path).resolve()
    if ROOT not in path.parents:
        return "Можно править только файлы внутри проекта Russel."
    if path.suffix not in (".py", ".html", ".md", ".json", ".ps1", ".txt", ".css", ".js"):
        return "Этот тип файла править нельзя."
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    if old:
        n = original.count(old)
        if n != 1:
            return f"Фрагмент old найден {n} раз(а); нужен ровно 1. Прочитай файл (read_own_code) и возьми точный кусок."
        updated = original.replace(old, new)
    else:
        if path.exists():
            return "old пустой — это допустимо только для нового файла."
        updated = new
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    ok, out = check_core()
    if not ok:
        if original or old:
            path.write_text(original, encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
        return f"Правка откачена: после неё ядро не импортируется.\n{out}"
    rev = commit(f"self: {reason}")
    return f"Правка применена, коммит {rev}. Чтобы она заработала, нужен перезапуск (инструмент restart_self)."


def run_evals(timeout: int = 900) -> tuple[int, int, str]:
    """Прогон evals/run.py в отдельном процессе (песочница). Возвращает (сдано, всего, отчёт о провалах)."""
    import re as _re
    try:
        r = subprocess.run([PY, "evals/run.py", "--quiet"], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env={**__import__("os").environ, "PYTHONUTF8": "1"})
    except subprocess.TimeoutExpired:
        return 0, 0, "экзамен не уложился по времени"
    m = _re.search(r"ИТОГ .*?: (\d+)/(\d+)", r.stdout)
    if not m:
        return 0, 0, (r.stdout + r.stderr)[-1500:]
    fails = "\n".join(l for l in r.stdout.splitlines() if l.startswith("❌"))
    return int(m.group(1)), int(m.group(2)), fails


def eval_baseline() -> int:
    try:
        return int((DATA_DIR / "eval_baseline.txt").read_text())
    except Exception:
        return 0


def save_baseline(score: int) -> None:
    (DATA_DIR / "eval_baseline.txt").write_text(str(score))


def rollback(steps: int = 1) -> str:
    target = f"HEAD~{steps}"
    files = git("diff", "--name-only", target, "HEAD")
    out = git("revert", "--no-edit", f"{target}..HEAD")
    return f"Откат на {steps} коммит(ов): {out[:500]}\nЗатронуты: {files}"


def request_restart() -> None:
    _restart_requested.set()


def restart_pending() -> bool:
    return _restart_requested.is_set()


def do_restart() -> None:
    import os
    os._exit(RESTART_EXIT_CODE)
