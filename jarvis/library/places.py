"""Важные места всего мира офлайн: аптеки, больницы, продукты, заправки, полиция… — только тип, название, координаты.

Источник — Overture Maps (places + divisions), читаем из открытого S3 только нужные столбцы: контакты, сайты,
соцсети и фото даже не скачиваются. Результат — <библиотека>\\maps\\places.db (SQLite + R*Tree + FTS5).
"""
from __future__ import annotations

import math
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .store import LIB_DIR

DB = LIB_DIR / "maps" / "places.db"
BUCKET = "overturemaps-us-west-2/release"

# группа → категории Overture (basic_category)
KINDS: dict[str, list[str]] = {
    "аптека": ["pharmacy_and_drug_store"],
    "больница": ["hospital", "general_hospital", "specialty_hospital", "emergency_department",
                 "emergency_or_urgent_care_facility", "urgent_care_clinic"],
    "поликлиника": ["primary_care_or_general_clinic", "walk_in_clinic", "outpatient_care_facility", "pediatric_clinic"],
    "стоматология": ["dental_clinic"],
    "продукты": ["food_and_beverage_store", "convenience_store", "discount_store", "warehouse_club_store",
                 "superstore", "department_store", "kiosk"],
    "рынок": ["market", "farmers_market"],
    "торговый центр": ["shopping_mall"],
    "хозтовары": ["hardware_home_and_garden_store"],
    "заправка": ["gas_station", "fueling_station"],
    "электрозарядка": ["ev_charging_station"],
    "автосервис": ["automotive_service"],
    "автозапчасти": ["vehicle_parts_store"],
    "полиция": ["police_station"],
    "пожарные": ["fire_station", "public_safety_service"],
    "банк": ["bank_or_credit_union"],
    "банкомат": ["atm"],
    "гостиница": ["hotel", "lodging", "inn", "bed_and_breakfast"],
    "кемпинг": ["campground", "rv_park"],
    "еда": ["restaurant", "casual_eatery", "fast_food_restaurant", "cafe", "coffee_shop", "food_court"],
    "вокзал": ["train_station"],
    "аэропорт": ["airport"],
    "транспорт": ["public_transit_facility_or_service", "ferry_service"],
    "госучреждение": ["government_office", "government_department", "courthouse"],
    "посольство": ["embassy"],
    "туалет": ["public_restroom"],
}
CAT2KIND = {c: i for i, (k, cats) in enumerate(KINDS.items()) for c in cats}
KIND_NAMES = list(KINDS)
SYNONYMS = {
    "аптек": "аптека", "лекарств": "аптека", "больниц": "больница", "скор": "больница", "травмпункт": "больница",
    "госпитал": "больница", "поликлиник": "поликлиника", "клиник": "поликлиника", "врач": "поликлиника",
    "стоматолог": "стоматология", "зуб": "стоматология", "магазин": "продукты", "продукт": "продукты",
    "супермаркет": "продукты", "еды купить": "продукты", "рын": "рынок", "базар": "рынок", "тц": "торговый центр",
    "торгов": "торговый центр", "хоз": "хозтовары", "строймат": "хозтовары", "инструмент": "хозтовары",
    "заправ": "заправка", "азс": "заправка", "бензин": "заправка", "топлив": "заправка", "зарядк": "электрозарядка",
    "автосервис": "автосервис", "сто": "автосервис", "шиномонтаж": "автосервис", "запчаст": "автозапчасти",
    "полиц": "полиция", "милиц": "полиция", "пожар": "пожарные", "мчс": "пожарные", "банкомат": "банкомат",
    "банк": "банк", "деньг": "банкомат", "гостиниц": "гостиница", "отел": "гостиница", "хостел": "гостиница",
    "ночлег": "гостиница", "переноч": "гостиница", "кемпинг": "кемпинг", "палатк": "кемпинг", "поесть": "еда",
    "кафе": "еда", "ресторан": "еда", "столов": "еда", "кофе": "еда", "вокзал": "вокзал", "поезд": "вокзал",
    "аэропорт": "аэропорт", "самол": "аэропорт", "автобус": "транспорт", "автовокзал": "транспорт",
    "паром": "транспорт", "акимиат": "госучреждение", "мэри": "госучреждение", "администрац": "госучреждение",
    "суд": "госучреждение", "госуч": "госучреждение", "посольств": "посольство", "консульств": "посольство",
    "туалет": "туалет",
}


def _s3():
    import pyarrow.fs as fs
    return fs.S3FileSystem(anonymous=True, region="us-west-2", connect_timeout=30, request_timeout=120)


def latest_release() -> str:
    import pyarrow.fs as fs
    s3 = _s3()
    rel = sorted(f.path.rsplit("/", 1)[-1] for f in s3.get_file_info(fs.FileSelector(BUCKET))
                 if f.type == fs.FileType.Directory)
    return rel[-1]


def _files(release: str, theme: str, typ: str) -> list[str]:
    import pyarrow.fs as fs
    return sorted(f.path for f in _s3().get_file_info(fs.FileSelector(f"{BUCKET}/{release}/theme={theme}/type={typ}/"))
                  if f.type == fs.FileType.File)


def _schema(db: sqlite3.Connection) -> None:
    db.executescript("""
    PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
    CREATE TABLE IF NOT EXISTS places(id INTEGER PRIMARY KEY, kind INTEGER, name TEXT, lat REAL, lon REAL,
                                      locality TEXT, country TEXT);
    CREATE TABLE IF NOT EXISTS towns(id INTEGER PRIMARY KEY, name TEXT, name_ru TEXT, name_en TEXT, country TEXT,
                                     subtype TEXT, population INTEGER, lat REAL, lon REAL);
    CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS done(part TEXT PRIMARY KEY);
    """)


HTTP = "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/"
TMP = LIB_DIR / "partial" / "overture"


def _local(path: str) -> Path:
    """Скачать файл целиком (быстрее, чем тянуть столбцы мелкими кусками через океан), с докачкой."""
    import httpx
    TMP.mkdir(parents=True, exist_ok=True)
    dst = TMP / path.rsplit("/", 1)[-1]
    url = HTTP + path.split("/", 1)[1]
    total = -1
    for attempt in range(6):
        try:
            total = int(httpx.head(url, timeout=60).headers["content-length"])
            have = dst.stat().st_size if dst.exists() else 0
            if have == total:
                return dst
            with httpx.stream("GET", url, headers={"Range": f"bytes={have}-"}, timeout=120) as r, dst.open("ab") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        except Exception:
            time.sleep(5 * (attempt + 1))
    if not dst.exists() or dst.stat().st_size != total:
        raise RuntimeError(f"не докачал {url}")
    return dst


def _places_part(path: str) -> list[tuple]:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    cols = ["names.primary", "basic_category", "confidence", "bbox.xmin", "bbox.ymin",
            "addresses.list.element.locality", "addresses.list.element.country", "operating_status"]
    loc = _local(path)
    pf = pq.ParquetFile(loc)
    cats = __import__("pyarrow").array(list(CAT2KIND))
    rows = []
    for g in range(pf.num_row_groups):              # порциями по группам строк — память не растёт
        t = pf.read_row_group(g, columns=cols)
        keep = pc.and_(pc.is_in(t["basic_category"], value_set=cats),
                       pc.fill_null(pc.greater_equal(t["confidence"], 0.5), False))
        keep = pc.and_(keep, pc.fill_null(pc.not_equal(t["operating_status"], "permanently_closed"), True))
        for r in t.filter(keep).to_pylist():
            name = (r["names"] or {}).get("primary")
            if not name:
                continue
            addr = (r["addresses"] or [{}])[0] or {}
            rows.append((CAT2KIND[r["basic_category"]], name[:120], round(r["bbox"]["ymin"], 6), round(r["bbox"]["xmin"], 6),
                         (addr.get("locality") or "")[:80], addr.get("country") or ""))
    del pf
    loc.unlink(missing_ok=True)
    return rows


def _towns_part(path: str) -> list[tuple]:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    loc = _local(path)
    pf = pq.ParquetFile(loc)
    kinds = __import__("pyarrow").array(["locality", "localadmin", "county", "region", "country", "borough", "macrohood", "neighborhood"])
    rows = []
    for g in range(pf.num_row_groups):
        t = pf.read_row_group(g, columns=["subtype", "country", "population", "names.primary", "names.common", "bbox.xmin", "bbox.ymin"])
        for r in t.filter(pc.is_in(t["subtype"], value_set=kinds)).to_pylist():
            n = r["names"] or {}
            common = dict(n.get("common") or [])
            if not n.get("primary"):
                continue
            rows.append((n["primary"][:120], (common.get("ru") or "")[:120], (common.get("en") or "")[:120], r["country"] or "",
                         r["subtype"], r["population"] or 0, round(r["bbox"]["ymin"], 6), round(r["bbox"]["xmin"], 6)))
    del pf
    loc.unlink(missing_ok=True)
    return rows


def _town_keys(db: sqlite3.Connection) -> None:
    """Ключи поиска городов в нижнем регистре (lower() в SQLite понимает только латиницу)."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(towns)")}
    for c in ("k_ru", "k_name", "k_en"):
        if c not in cols:
            db.execute(f"ALTER TABLE towns ADD COLUMN {c} TEXT")
    db.create_function("ulower", 1, lambda x: (x or "").casefold().replace("ё", "е"), deterministic=True)
    db.execute("UPDATE towns SET k_ru=ulower(name_ru), k_name=ulower(name), k_en=ulower(name_en)")
    for c in ("k_ru", "k_name", "k_en"):
        db.execute(f"CREATE INDEX IF NOT EXISTS towns_{c} ON towns({c})")
    db.commit()


def build(release: str | None = None, workers: int = 2, log=print) -> Path:
    release = release or latest_release()
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    _schema(db)
    old = db.execute("SELECT v FROM meta WHERE k='release'").fetchone()
    if old and old[0] != release:                      # новый выпуск — собираем заново
        db.executescript("DELETE FROM places; DELETE FROM towns; DELETE FROM done;")
    db.execute("INSERT OR REPLACE INTO meta VALUES('release', ?)", (release,))
    db.commit()
    done = {r[0] for r in db.execute("SELECT part FROM done")}
    jobs = [("places", p) for p in _files(release, "places", "place")] + \
           [("towns", p) for p in _files(release, "divisions", "division")]
    jobs = [j for j in jobs if j[1] not in done]
    log(f"Overture {release}: частей к обработке {len(jobs)}")
    t0 = time.time()

    def run(job):
        kind, path = job
        return kind, path, (_places_part if kind == "places" else _towns_part)(path)

    with ThreadPoolExecutor(workers) as ex:
        for n, (kind, path, rows) in enumerate(ex.map(run, jobs), 1):
            if kind == "places":
                db.executemany("INSERT INTO places(kind,name,lat,lon,locality,country) VALUES(?,?,?,?,?,?)", rows)
            else:
                db.executemany("INSERT INTO towns(name,name_ru,name_en,country,subtype,population,lat,lon) "
                               "VALUES(?,?,?,?,?,?,?,?)", rows)
            db.execute("INSERT INTO done VALUES(?)", (path,))
            db.commit()
            log(f"  [{n}/{len(jobs)}] {kind}: +{len(rows)} за {time.time() - t0:.0f} с")
    log("Индексы…")
    db.executescript("""
    DROP TABLE IF EXISTS geo; DROP TABLE IF EXISTS names_fts;
    CREATE VIRTUAL TABLE geo USING rtree(id, minlat, maxlat, minlon, maxlon);
    INSERT INTO geo SELECT id, lat, lat, lon, lon FROM places;
    CREATE VIRTUAL TABLE names_fts USING fts5(name, content='places', content_rowid='id', tokenize='unicode61');
    INSERT INTO names_fts(rowid, name) SELECT id, name FROM places;
    """)
    _town_keys(db)
    db.commit()
    n = db.execute("SELECT count(*) FROM places").fetchone()[0]
    m = db.execute("SELECT count(*) FROM towns").fetchone()[0]
    db.execute("VACUUM")
    db.close()
    log(f"ГОТОВО: мест {n}, населённых пунктов и районов {m}, {DB.stat().st_size / 1e9:.2f} ГБ")
    return DB


# ─── поиск ────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def kind_of(text: str) -> str | None:
    t = text.lower()
    if t in KINDS:
        return t
    for stem, k in sorted(SYNONYMS.items(), key=lambda x: -len(x[0])):
        if re.search(r"(?<![а-яё])" + stem, t):
            return k
    return None


def geocode(place: str) -> tuple[float, float, str] | None:
    """Название города/села/района → координаты (крупнейшее совпадение)."""
    q = place.strip().casefold().replace("ё", "е")
    if not q or not DB.exists():
        return None
    with _conn() as db:
        rows = db.execute("SELECT lat, lon, coalesce(nullif(name_ru,''), name), country, subtype, population FROM towns "
                          "WHERE k_ru=? OR k_name=? OR k_en=? LIMIT 200", (q, q, q)).fetchall()
    if not rows:
        return None
    city = {"locality", "localadmin"}
    # город с наибольшим населением; иначе — самое населённое из прочего; иначе — первый город
    best = (max((r for r in rows if r[4] in city and r[5]), key=lambda r: r[5], default=None)
            or max((r for r in rows if r[5]), key=lambda r: r[5], default=None)
            or next((r for r in rows if r[4] in city), rows[0]))
    return best[0], best[1], f"{best[2]} ({best[3]})" if best[3] else best[2]


def distance_km(a: float, b: float, c: float, d: float) -> float:
    p = math.pi / 180
    h = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def nearby(lat: float, lon: float, kind: str | None = None, name: str = "", limit: int = 5,
           max_km: float = 50) -> list[dict]:
    kid = KIND_NAMES.index(kind) if kind in KINDS else None
    with _conn() as db:
        r = 0.02
        while True:                                  # расширяем окно, пока не наберём достаточно
            dlon = r / max(0.05, math.cos(math.radians(lat)))
            sql = ("SELECT p.id, p.kind, p.name, p.lat, p.lon, p.locality, p.country FROM geo g JOIN places p ON p.id=g.id "
                   "WHERE g.minlat>=? AND g.maxlat<=? AND g.minlon>=? AND g.maxlon<=?")
            args: list = [lat - r, lat + r, lon - dlon, lon + dlon]
            if kid is not None:
                sql += " AND p.kind=?"
                args.append(kid)
            if name:
                sql += " AND p.id IN (SELECT rowid FROM names_fts WHERE names_fts MATCH ?)"
                args.append(" ".join(f'"{w}"*' for w in re.findall(r"\w+", name)))
            rows = db.execute(sql + " LIMIT 5000", args).fetchall()
            if len(rows) >= limit or r * 111 >= max_km:
                break
            r *= 2.5
    out = [{"name": n, "kind": KIND_NAMES[k], "lat": la, "lon": lo, "locality": loc, "country": c,
            "km": round(distance_km(lat, lon, la, lo), 2)} for _, k, n, la, lo, loc, c in rows]
    out.sort(key=lambda x: x["km"])
    return [o for o in out if o["km"] <= max_km][:limit]


if __name__ == "__main__":
    build()
