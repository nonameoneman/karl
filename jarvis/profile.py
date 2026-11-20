"""Профиль пользователя: стиль ответов, «Большая пятёрка» (Mini-IPIP, IPIP — общественное достояние), цели.

Всё хранится локально в data/profile.json, анкета — только с согласия, не больше DAILY вопросов в день.
Профиль используется открыто: пользователь видит, что Карл понял и как подстраивается, и может всё стереть.
"""
from __future__ import annotations

import datetime as dt
import json
import threading

from . import bus
from .config import DATA_DIR
from .tools import tool

PATH = DATA_DIR / "profile.json"
DAILY = 3
_lock = threading.Lock()

# Mini-IPIP (Donnellan et al., 2006): 20 утверждений, по 4 на черту; key -1 — обратный вопрос.
# E — общительность, A — доброжелательность, C — организованность, N — тревожность, O — воображение/интеллект.
ITEMS = [
    ("e1", "E", +1), ("a1", "A", +1), ("c1", "C", +1), ("n1", "N", +1), ("o1", "O", +1),
    ("e2", "E", -1), ("a2", "A", -1), ("c2", "C", -1), ("n2", "N", -1), ("o2", "O", -1),
    ("e3", "E", +1), ("a3", "A", +1), ("c3", "C", +1), ("n3", "N", +1), ("o3", "O", -1),
    ("e4", "E", -1), ("a4", "A", -1), ("c4", "C", -1), ("n4", "N", -1), ("o4", "O", -1),
]
_KEY = {i: (t, k) for i, t, k in ITEMS}
STYLE = {"length": ("short", "normal", "long"), "depth": ("simple", "normal", "expert")}


def _empty() -> dict:
    return {"consent": None, "style": {}, "answers": {}, "goals": [], "day": "", "asked": 0}


def load() -> dict:
    try:
        return {**_empty(), **json.loads(PATH.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return _empty()


def save(p: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PATH)


def traits(p: dict | None = None) -> dict[str, int]:
    """Оценки черт 0–100 — только по чертам, где ответов хотя бы два."""
    p = p or load()
    acc: dict[str, list[int]] = {}
    for item, v in p["answers"].items():
        if item in _KEY:
            t, k = _KEY[item]
            acc.setdefault(t, []).append(v if k > 0 else 6 - v)
    return {t: round((sum(vs) / len(vs) - 1) * 25) for t, vs in acc.items() if len(vs) >= 2}


def adapt_keys(p: dict | None = None) -> list[str]:
    """Как подстраиваться: «E+» — черта заметно выше середины, «E-» — ниже."""
    out = []
    for t, s in traits(p).items():
        if s >= 65:
            out.append(t + "+")
        elif s <= 35:
            out.append(t + "-")
    return sorted(out)


_ADAPT_RU = {
    "E+": "можно живее, с лёгким юмором к месту",
    "E-": "спокойно и сдержанно, без лишней болтовни и шуток",
    "A+": "мягко и тепло",
    "A-": "прямо и по фактам, без сюсюканья",
    "C+": "структурно: шаги по порядку, чётко",
    "C-": "разбивай дела на маленькие шаги и предлагай напоминания, чтобы ничего не забылось",
    "N+": "спокойно и ободряюще: не драматизируй, всегда давай понятный следующий шаг",
    "N-": "можно говорить о рисках прямо",
    "O+": "можно идеи, аналогии и необычные варианты",
    "O-": "практично и конкретно: проверенные способы, без абстракций",
}
_STYLE_RU = {
    ("length", "short"): "отвечай очень коротко — 1–2 предложения",
    ("length", "long"): "можно подробнее, с объяснением почему",
    ("depth", "simple"): "объясняй простыми словами, без терминов, с бытовыми примерами",
    ("depth", "expert"): "можно термины и технические детали, как специалисту",
}


def prompt_block() -> str:
    """Добавка к системному промпту: стиль, подстройка, цели. Пусто — ничего не знаем."""
    p = load()
    lines = [_STYLE_RU[(k, v)] for k, v in p["style"].items() if (k, v) in _STYLE_RU]
    lines += [_ADAPT_RU[k] for k in adapt_keys(p)]
    out = ""
    if lines:
        out += "\nКак общаться с этим пользователем (он сам ответил на анкету): " + "; ".join(lines) + "."
    if p["goals"]:
        out += ("\nЦели, которые пользователь сам себе поставил: " + "; ".join(g["text"] for g in p["goals"])
                + ". Если разговор касается темы цели или пользователь спрашивает, чем заняться, — предложи один "
                  "маленький конкретный шаг. Не навязывайся и не дави.")
    return out


def next_question(force: bool = False) -> dict | None:
    """Что спросить сейчас: согласие → стиль → вопросы анкеты; не больше DAILY в день (force — сам попросил)."""
    with _lock:
        p = load()
        today = dt.date.today().isoformat()
        if p["day"] != today:
            p["day"], p["asked"] = today, 0
            save(p)
        if p["consent"] is False or (p["asked"] >= DAILY and not force):
            return None
        if p["consent"] is None:
            return {"kind": "consent"}
        for k in STYLE:
            if k not in p["style"]:
                return {"kind": "style", "id": k, "options": list(STYLE[k])}
        from .config import settings
        if not settings.home_city and not p.get("home_asked"):
            return {"kind": "home"}
        left = [i for i, _, _ in ITEMS if i not in p["answers"]]
        if not left:
            return None
        return {"kind": "item", "id": left[0], "n": len(ITEMS) - len(left) + 1, "total": len(ITEMS)}


def answer(kind: str, qid: str, value, edit: bool = False) -> bool:
    with _lock:
        p = load()
        if kind == "consent":
            p["consent"] = bool(value)
        elif kind == "style" and qid in STYLE and value in STYLE[qid]:
            p["style"][qid] = value
        elif kind == "item" and qid in _KEY and int(value) in range(1, 6):
            p["answers"][qid] = int(value)
        elif kind == "home":
            p["home_asked"] = True
            if str(value or "").strip():
                set_home(str(value).strip())
        elif kind == "skip":
            p["asked"] = DAILY                         # «не сейчас» — до завтра
            save(p)
            return True
        else:
            return False
        if kind != "consent" and not edit:
            p["asked"] += 1
        save(p)
    return True


def set_home(city: str) -> str:
    """Домашний город: по офлайн-справочнику — «Город (KG)» с координатами; нет справочника — как написал."""
    from .config import settings
    try:
        from .library import places
        loc = places.geocode(city)
    except Exception:
        loc = None
    if loc:
        settings.home_lat, settings.home_lon, settings.home_city = loc
    else:
        settings.home_city = city
    settings.save()
    return settings.home_city


def view() -> dict:
    p = load()
    return {"consent": p["consent"], "style": p["style"], "answered": len(p["answers"]), "total": len(ITEMS),
            "answers": p["answers"], "traits": traits(p), "adapt": adapt_keys(p), "goals": p["goals"]}


def forget() -> None:
    with _lock:
        PATH.unlink(missing_ok=True)


def add_goal_text(text: str) -> None:
    with _lock:
        p = load()
        if not any(g["text"].lower() == text.lower() for g in p["goals"]):
            p["goals"].append({"text": text, "since": dt.date.today().isoformat()})
        save(p)


def remove_goal_index(i: int) -> str | None:
    with _lock:
        p = load()
        if 0 <= i < len(p["goals"]):
            g = p["goals"].pop(i)
            save(p)
            return g["text"]
    return None


# ─── инструменты для разговора ───────────────────────────────────────────

@tool("green")
def show_profile() -> str:
    """Показать, что Карл знает о пользователе и как под него подстраивается (анкета, стиль, цели) —
    «что ты обо мне знаешь», «мой профиль», «какой у меня характер по анкете»."""
    bus.publish({"type": "profile"})
    v = view()
    names = {"E": "общительность", "A": "доброжелательность", "C": "организованность",
             "N": "тревожность", "O": "открытость новому"}
    parts = [f"{names[t]} {s} из 100" for t, s in v["traits"].items()]
    txt = f"Анкета: отвечено {v['answered']} из {v['total']}."
    if parts:
        txt += " По ответам: " + ", ".join(parts) + "."
    if v["goals"]:
        txt += " Цели: " + "; ".join(g["text"] for g in v["goals"]) + "."
    return txt + " Профиль открыт в окне; там же его можно изменить или удалить."


@tool("green")
def set_answer_style(length: str = "", depth: str = "") -> str:
    """Запомнить, как пользователю удобнее получать ответы: «говори короче», «объясняй подробнее»,
    «попроще, без терминов», «можно технически».

    Args:
        length: short (коротко), normal или long (подробно); пусто — не менять
        depth: simple (простыми словами), normal или expert (как специалисту); пусто — не менять
    """
    done = []
    for k, v in (("length", length), ("depth", depth)):
        if v:
            if v not in STYLE[k]:
                return f"Неизвестное значение {k}={v}."
            with _lock:
                p = load()
                p["style"][k] = v
                save(p)
            done.append(v)
    return "Запомнил, буду отвечать так." if done else "Нечего менять."


@tool("green")
def add_goal(text: str) -> str:
    """Записать цель, которую пользователь САМ ставит себе («хочу выучить английский», «бросаю курить»,
    «хочу больше двигаться»). Карл будет к месту предлагать маленькие шаги.

    Args:
        text: цель словами пользователя
    """
    add_goal_text(text.strip())
    return f"Записал цель: «{text.strip()}». Буду иногда предлагать маленькие шаги — скажи, если надоест."


@tool("green")
def remove_goal(text: str) -> str:
    """Убрать цель пользователя («больше не напоминай про английский», «цель выполнена»).

    Args:
        text: цель или слово из неё
    """
    goals = load()["goals"]
    for i, g in enumerate(goals):
        if text.lower() in g["text"].lower() or g["text"].lower() in text.lower():
            remove_goal_index(i)
            return f"Убрал цель: «{g['text']}»."
    return "Такой цели нет." + (" Есть: " + "; ".join(g["text"] for g in goals) if goals else "")


@tool("yellow")
def forget_profile() -> str:
    """Удалить всю анкету, стиль и цели пользователя («забудь всё про мой характер», «удали профиль»)."""
    forget()
    bus.publish({"type": "profile"})
    return "Профиль удалён: анкета, стиль и цели стёрты."
