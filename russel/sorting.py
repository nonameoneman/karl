"""Сортировка по смыслу: Russel смотрит на имена, даты, начало текстов и содержимое картинок,
составляет план (какие файлы в какие папки), озвучивает его и применяет только после согласия."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
from pathlib import Path

from . import bus, journal
from .builtin_tools import resolve, _size
from .config import DATA_DIR, settings
from .tools import tool

_IMG = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_TXT = {".txt", ".md", ".csv"}
_SECRET = {".pem", ".key", ".pfx", ".p12", ".env", ".kdbx"}
_VISION_CACHE = DATA_DIR / "vision_cache.json"
_last_plan: dict | None = None


def _vision_cache() -> dict:
    try:
        return json.loads(_VISION_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def describe_image(p: Path, cache: dict) -> str:
    st = p.stat()
    key = hashlib.md5(f"{p}|{st.st_mtime}|{st.st_size}".encode()).hexdigest()
    if key in cache:
        return cache[key]
    from .llm import _client
    data = p.read_bytes()
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.thumbnail((768, 768))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=85)
        data = buf.getvalue()
    except Exception:
        pass
    r = _client.chat(model=settings.model, think=False, keep_alive="30m", options={"num_ctx": 4096, "temperature": 0.2},
                     messages=[{"role": "user", "images": [data],
                                "content": "Одной короткой фразой по-русски: что на изображении и к какой теме/проекту "
                                           "это может относиться. Если виден текст или логотип — назови его."}])
    desc = (r.message.content or "").strip().replace("\n", " ")[:200]
    cache[key] = desc
    return desc


def _hint(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in _SECRET:
        return "секретный файл (не открывал)"
    if p.is_dir():
        try:
            kids = sorted(x.name for x in p.iterdir())[:6]
            return "папка: " + ", ".join(kids)
        except OSError:
            return "папка"
    if ext in _TXT and p.stat().st_size < 2_000_000:
        try:
            return "начало: " + p.read_text(encoding="utf-8", errors="ignore")[:160].replace("\n", " ")
        except OSError:
            return ""
    return ""


_PLAN_PROMPT = """Ты раскладываешь файлы пользователя по папкам по смыслу.
Цель пользователя: {goal}
{only}
Элементы в папке {root} (номер | имя | тип | размер | изменён | подсказка):
{items}

Правила:
- Группируй по логике: проект/клиент, тема, назначение. Используй имена, даты, описания картинок, начало текстов.
- Названия папок короткие и понятные, по-русски или как называется проект (Skynet, Диплом). От 2 до 10 папок.
- Существующие папки-проекты можно переносить внутрь новой папки только если это явно уместно; лучше оставляй их.
- Ярлыки .lnk, desktop.ini и то, что не подходит под цель, — оставляй на месте (не включай в moves).
- Если элемент можно положить в УЖЕ СУЩЕСТВУЮЩУЮ подходящую папку из списка — укажи её точное имя.
Ответь строго JSON:
{{"folders": [{{"name": "...", "why": "коротко, что внутри"}}], "moves": [{{"i": номер, "folder": "имя папки"}}]}}"""


@tool("green")
def plan_sort(path: str, goal: str, only: str = "", look_at_images: bool = True) -> str:
    """Составить план сортировки папки ПО СМЫСЛУ: проекты, темы, содержимое картинок, названия, даты.
    Также чтобы собрать файлы одного проекта (only="Skynet"). Ничего не перемещает — показывает план;
    после согласия пользователя вызови apply_sort_plan. Для сортировки просто по расширениям есть organize_folder.

    Args:
        path: папка (Рабочий стол, Загрузки или путь)
        goal: чего хочет пользователь, его словами
        only: если нужно собрать только файлы одной темы/проекта — её название, иначе пусто
        look_at_images: смотреть ли содержимое картинок (дольше, но точнее)
    """
    global _last_plan
    from .llm import _client
    root = resolve(path)
    if not only:
        only = _detect_only(goal)
    if not root.is_dir():
        return f"Папка не найдена: {root}"
    entries = sorted([p for p in root.iterdir() if p.name.lower() not in ("desktop.ini",)],
                     key=lambda p: p.name.lower())
    cache = _vision_cache()
    imgs = [p for p in entries if p.is_file() and p.suffix.lower() in _IMG and p.stat().st_size < 25_000_000]
    descs: dict[Path, str] = {}
    if look_at_images and imgs:
        bus.publish({"type": "status", "text": f"Рассматриваю {min(len(imgs), 40)} картинок…"})
        for p in imgs[:40]:
            try:
                descs[p] = describe_image(p, cache)
            except Exception as e:
                descs[p] = f"(не разглядел: {e})"
        _VISION_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    lines, texts = [], {}
    for i, p in enumerate(entries):
        try:
            st = p.stat()
        except OSError:
            continue
        kind = "папка" if p.is_dir() else (p.suffix.lower() or "файл")
        when = dt.datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%y")
        hint = descs.get(p) or _hint(p)
        lines.append(f"{i} | {p.name} | {kind} | {'' if p.is_dir() else _size(st.st_size)} | {when} | {hint}")
        texts[i] = f"{p.name} {hint}".lower()
    if only:
        return _plan_only(root, entries, lines, texts, only, goal)
    only_txt = (f"ВАЖНО: собрать ТОЛЬКО то, что относится к «{only}» (по имени, содержимому, описанию картинки). "
                f"Всё остальное не трогать.") if only else ""
    bus.publish({"type": "status", "text": "Составляю план…"})
    r = _client.chat(model=settings.model, format="json", think=False, keep_alive="30m",
                     options={"num_ctx": 16384, "temperature": 0.2},
                     messages=[{"role": "user", "content": _PLAN_PROMPT.format(
                         goal=goal, only=only_txt, root=root, items="\n".join(lines))}])
    raw = r.message.content or ""
    try:
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception:
        return "Не получилось составить план (модель вернула не JSON). Попробуй ещё раз или уточни цель."
    existing = {p.name for p in entries if p.is_dir()}
    moves: list[tuple[Path, str]] = []
    seen: set[int] = set()
    for m in data.get("moves", []):
        try:
            idx = int(m["i"])
            src = entries[idx]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if idx in seen:
            continue
        seen.add(idx)
        folder = re.sub(r'[<>:"/\\|?*]', "", str(m.get("folder", ""))).strip().strip(".")
        if not folder or folder == src.name or src.suffix.lower() == ".lnk":
            continue
        moves.append((src, folder))
    if not moves:
        return "Под эту цель ничего не нашёл — оставляю всё как есть."
    why = {f.get("name"): f.get("why", "") for f in data.get("folders", []) if isinstance(f, dict)}
    return _store_plan(root, entries, moves, why, existing)


_GENERIC = {"russel", "json", "pdf", "png", "jpg", "svg", "zip", "txt", "csv", "xlsx", "mp4", "ok"}


def _detect_only(goal: str) -> str:
    """«собери файлы проекта Skynet» → Skynet. Берёт явное название: латиница/цифры или слово в кавычках."""
    m = re.search(r"[«\"']([^»\"']{2,40})[»\"']", goal)
    if m:
        return m.group(1).strip()
    if not re.search(r"(проект|про |по |связан|относ|только|собери|файлы)", goal.lower()):
        return ""
    cands = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9.\-_]{1,30}", goal) if w.lower() not in _GENERIC]
    return cands[0] if cands else ""


def _store_plan(root: Path, entries: list[Path], moves: list[tuple[Path, str]], why: dict, existing: set) -> str:
    global _last_plan
    _last_plan = {"root": str(root), "moves": [(str(s), f) for s, f in moves], "turn": journal.current_turn()}
    groups: dict[str, list[str]] = {}
    for s, f in moves:
        groups.setdefault(f, []).append(s.name)
    out = [f"План для {root.name}: переложить {len(moves)} из {len(entries)}, остальное не трогаю."]
    for f, names in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        tag = " (уже есть)" if f in existing else ""
        out.append(f"• {f}{tag} — {len(names)}: {', '.join(names[:6])}{' …' if len(names) > 6 else ''}"
                   + (f" — {why[f]}" if why.get(f) else ""))
    out.append("Скажи «да» — применю (apply_sort_plan), или скажи, что поменять.")
    bus.publish({"type": "plan", "text": "\n".join(out)})
    return "\n".join(out)


_ONLY_PROMPT = """Пользователь хочет собрать в одну папку ТОЛЬКО то, что относится к «{only}». Цель: {goal}
Уже точно относятся (по имени или содержимому): {sure}
Остальные элементы (номер | имя | тип | размер | изменён | подсказка):
{items}

Какие ЕЩЁ элементы явно относятся к «{only}»? Только с очевидной связью (название, логотип, текст про {only}).
Сомневаешься — не включай. Ответь строго JSON: {{"extra": [номера], "folder": "название папки"}}"""


def _plan_only(root: Path, entries: list[Path], lines: list[str], texts: dict[int, str], only: str, goal: str) -> str:
    from .llm import _client
    key = only.lower().strip()
    variants = {key, key.replace(" ", ""), key.replace(" ", "_"), key.replace(" ", "-")}
    sure = [i for i, t in texts.items() if any(v in t for v in variants)]
    sure = [i for i in sure if entries[i].suffix.lower() != ".lnk"]
    rest = [ln for ln in lines if int(ln.split(" | ", 1)[0]) not in sure]
    folder = only.strip()
    extra: list[int] = []
    try:
        r = _client.chat(model=settings.model, think=False, keep_alive="30m", options={"num_ctx": 16384, "temperature": 0.1},
                         messages=[{"role": "user", "content": _ONLY_PROMPT.format(
                             only=only, goal=goal, sure=", ".join(entries[i].name for i in sure) or "ничего",
                             items="\n".join(rest))}])
        raw = r.message.content or ""
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        extra = [int(i) for i in data.get("extra", []) if str(i).isdigit() and int(i) < len(entries)]
        folder = re.sub(r'[<>:"/\\|?*]', "", str(data.get("folder") or folder)).strip() or folder
    except Exception:
        pass
    extra = [i for i in extra if i not in sure and entries[i].suffix.lower() != ".lnk"][:15]
    chosen = sure + extra
    if not chosen:
        return f"Не нашёл ничего про «{only}» в {root.name}."
    existing = {p.name for p in entries if p.is_dir()}
    if folder in existing:                      # папка проекта уже есть — кладём в неё
        chosen = [i for i in chosen if entries[i].name != folder]
    moves = [(entries[i], folder) for i in chosen]
    why = {folder: f"по названию/содержимому: {len(sure)}, по смыслу: {len(extra)}"}
    return _store_plan(root, entries, moves, why, existing)


@tool("green")
def apply_sort_plan() -> str:
    """Применить последний план из plan_sort (только после согласия пользователя). Обратимо: «верни как было»."""
    global _last_plan
    if not _last_plan:
        return "Плана нет — сначала plan_sort."
    if _last_plan.get("turn") == journal.current_turn():
        return ("НЕ ПРИМЕНЕНО: пользователь ещё не видел план. Перескажи ему план коротко и спроси, применять ли. "
                "Применяй только после его «да» в следующем сообщении.")
    root = Path(_last_plan["root"])
    done, errors = 0, []
    for src, folder in _last_plan["moves"]:
        s = Path(src)
        if not s.exists():
            errors.append(f"{s.name}: уже нет")
            continue
        try:
            journal.move(s, root / folder / s.name)
            done += 1
        except Exception as e:
            errors.append(f"{s.name}: {e}")
    _last_plan = None
    return f"Переложил {done} элементов." + (f" Ошибки: {'; '.join(errors[:5])}" if errors else "") + \
        " Откатить — «верни как было»."
