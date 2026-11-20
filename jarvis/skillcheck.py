"""Проверка файла навыка из командной строки: python -m jarvis.skillcheck skills/_staging/name.py"""
import sys
from pathlib import Path

from .selfupdate import _lint_skill, validate_skill_file

if __name__ == "__main__":
    p = Path(sys.argv[1]).resolve()
    problem = _lint_skill(p.read_text(encoding="utf-8"))
    if problem:
        print("FAIL:", problem)
        sys.exit(1)
    ok, out = validate_skill_file(p)
    print(("OK: " if ok else "FAIL: ") + out)
    sys.exit(0 if ok else 1)
