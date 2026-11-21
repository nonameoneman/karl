"""Инструменты Рассела для карт: найти рядом, маршрут, показать на карте, запомнить «мой город»."""
from __future__ import annotations

import math
import urllib.parse
import webbrowser

import httpx

from .. import bus
from ..config import settings
from ..tools import tool
from . import places

_DIRS = ["севернее", "северо-восточнее", "восточнее", "юго-восточнее", "южнее", "юго-западнее", "западнее", "северо-западнее"]


def _bearing(a, b, c, d) -> str:
    y = math.sin(math.radians(d - b)) * math.cos(math.radians(c))
    x = math.cos(math.radians(a)) * math.sin(math.radians(c)) - math.sin(math.radians(a)) * math.cos(math.radians(c)) * math.cos(math.radians(d - b))
    deg = (math.degrees(math.atan2(y, x)) + 360) % 360
    return _DIRS[int((deg + 22.5) // 45) % 8]


def _where(where: str) -> tuple[float, float, str] | None:
    if where.strip():
        return places.geocode(where)
    if settings.home_lat or settings.home_lon:
        return settings.home_lat, settings.home_lon, settings.home_city or "дом"
    return None


def _dist(km: float) -> str:
    return f"{round(km * 1000, -1):.0f} м" if km < 1 else f"{km:.1f} км".replace(".", ",")


@tool("green")
def find_places(what: str, where: str = "", limit: int = 5) -> str:
    """Найти рядом важные места по офлайн-карте мира (без интернета): аптека, больница, поликлиника, продукты,
    рынок, торговый центр, хозтовары, заправка, автосервис, полиция, пожарные, банк, банкомат, гостиница, кемпинг,
    еда, вокзал, аэропорт, автовокзал, госучреждение, посольство, туалет — или по названию («Глобус», «Народный»).
    Показывает найденное на карте в окне.

    Args:
        what: что искать — тип места или название
        where: город/село/район; пусто — рядом с домом пользователя
        limit: сколько мест вернуть (по умолчанию 5)
    """
    if not places.DB.exists():
        return "Офлайн-база мест ещё не собрана."
    loc = _where(where)
    if not loc:
        return ("Не знаю, где искать: назови город (например, «аптеки в Лос-Анджелесе») или скажи «мой город — …», "
                "и я запомню.") if not where else f"Не нашёл на карте место «{where}»."
    lat, lon, label = loc
    kind = places.kind_of(what)
    name = "" if kind else what
    found = places.nearby(lat, lon, kind=kind, name=name, limit=max(1, min(int(limit or 5), 15)))
    if not found:
        return f"Рядом с «{label}» в радиусе 50 км не нашёл: {what}."
    bus.publish({"type": "map", "center": [lat, lon], "label": label,
                 "points": [{"name": f["name"], "lat": f["lat"], "lon": f["lon"], "kind": f["kind"], "km": f["km"]}
                            for f in found]})
    lines = [f"{label}: ближайшие — {kind or what}"]
    for i, f in enumerate(found, 1):
        where_txt = f", {f['locality']}" if f["locality"] else ""
        lines.append(f"{i}. {f['name']} ({f['kind']}){where_txt} — {_dist(f['km'])}, "
                     f"{_bearing(lat, lon, f['lat'], f['lon'])}")
    lines.append("Данные: Overture Maps (офлайн-копия); часы работы и наличие товаров не известны.")
    return "\n".join(lines)


def _online() -> bool:
    try:
        return httpx.head("https://www.google.com/maps", timeout=2.5).status_code < 500
    except Exception:
        return False


@tool("green")
def route(to: str, start: str = "") -> str:
    """Маршрут: «построй маршрут до больницы», «как доехать до травмпункта от улицы Мира 1», «дорога до вокзала».
    Находит ближайшее подходящее место (или место по названию). С интернетом открывает маршрут в Google Maps
    от адреса пользователя; без интернета показывает место на офлайн-карте с расстоянием и направлением.

    Args:
        to: куда — тип места («больница», «травмпункт», «аптека») или название
        start: откуда — адрес или место; пусто — дом пользователя
    """
    home = _where("")
    origin = places.geocode(start) if start.strip() else home
    base = origin or home                     # улиц в офлайн-справочнике нет — ищем от города пользователя
    kind = places.kind_of(to)
    dest = None
    if base and places.DB.exists():
        found = places.nearby(base[0], base[1], kind=kind, name="" if kind else to, limit=3)
        if found:
            dest = found[0]
    if dest is None and not kind:
        g = places.geocode(to)
        if g:
            dest = {"name": g[2], "lat": g[0], "lon": g[1], "kind": "", "km": 0, "locality": ""}
    if dest is None:
        return f"Не нашёл, куда строить маршрут: {to}."
    online = _online()
    if online:
        if start.strip():
            o = start if origin or not settings.home_city else f"{start}, {settings.home_city.split(' (')[0]}"
        elif home:
            o = f"{home[0]},{home[1]}"
        else:
            o = ""
        url = ("https://www.google.com/maps/dir/?api=1&travelmode=driving"
               + (f"&origin={urllib.parse.quote(o)}" if o else "")
               + f"&destination={dest['lat']},{dest['lon']}")
        webbrowser.open(url)
    if base:
        bus.publish({"type": "map", "center": [base[0], base[1]], "label": base[2],
                     "points": [{"name": dest["name"], "lat": dest["lat"], "lon": dest["lon"],
                                 "kind": dest["kind"], "km": dest.get("km", 0)}]})
    near = (f" — {_dist(dest['km'])}, {_bearing(base[0], base[1], dest['lat'], dest['lon'])} центра «{base[2]}»"
            if base and dest.get("km") else "")
    if online:
        return f"Открыл маршрут в Google Maps до «{dest['name']}»{near}. Время в пути и пробки — там."
    return (f"Без интернета дорожный маршрут не построю. Ближайшее: «{dest['name']}»{near}; показал на офлайн-карте. "
            "Если есть интернет — скажи ещё раз, открою маршрут.")


@tool("green")
def show_map(where: str = "") -> str:
    """Показать офлайн-карту в окне: город, страну или место. Пусто — дом пользователя.

    Args:
        where: город, страна или место
    """
    loc = _where(where)
    if not loc:
        return f"Не нашёл на карте «{where}»." if where else "Назови место, которое показать."
    lat, lon, label = loc
    bus.publish({"type": "map", "center": [lat, lon], "label": label, "points": []})
    return f"Показываю на карте: {label}."


@tool("green")
def set_home_city(city: str) -> str:
    """Запомнить, где живёт пользователь, — для поиска мест рядом без уточнений («мой город — Лос-Анджелес»).

    Args:
        city: город или село
    """
    loc = places.geocode(city)
    if not loc:
        return f"Не нашёл «{city}» в офлайн-справочнике населённых пунктов."
    settings.home_city, settings.home_lat, settings.home_lon = loc[2], loc[0], loc[1]
    settings.save()
    return f"Запомнил: ваш город — {loc[2]}."
