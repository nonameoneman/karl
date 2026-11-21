"""Загрузка библиотеки из консоли: python -m russel.library.cli get essential|full|core|<id> …   |   status"""
from __future__ import annotations

import sys
import time

from . import add_entry, catalog, library_status, store


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] == "status":
        print(library_status())
        return
    args = sys.argv[2:]
    cat = catalog()
    if args == ["canon"]:
        want = [c for c in cat if c.get("canon")]
    elif args in (["essential"], ["full"]):
        want = [c for c in cat if c.get("bundle") == "essential" or args == ["full"]]
    elif args == ["core"]:
        want = [c for c in cat if c.get("tier") == "core"]
    else:
        want = [c for c in cat if c["id"] in args]
    have = {i["id"]: i for i in store.load_manifest()["items"]}
    want = [c for c in want if c["id"] not in have
            or (c.get("version_date") or "") > (have[c["id"]].get("version_date") or "")]
    want.sort(key=lambda c: c.get("size_mb") or 1)           # сначала маленькое: основа будет готова быстро
    print(f"К загрузке {len(want)} источников, ~{sum(c.get('size_mb') or 0 for c in want) / 1024:.1f} ГБ", flush=True)
    ok = fail = 0
    for n, c in enumerate(want, 1):
        t0 = time.time()
        print(f"[{n}/{len(want)}] {c['id']} ({c.get('size_mb') or '?'} МБ)…", flush=True)
        try:
            res = add_entry(c)
            ok += 1
            print(f"    ✓ {res} за {time.time() - t0:.0f} с", flush=True)
        except Exception as e:
            fail += 1
            print(f"    ✗ {type(e).__name__}: {e}", flush=True)
    print(f"ГОТОВО: успешно {ok}, ошибок {fail}", flush=True)


if __name__ == "__main__":
    main()
