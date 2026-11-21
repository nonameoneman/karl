"""Офлайн-карты: вырезка из мировой карты Protomaps (PMTiles) только нужной детализации.

Весь мир до z15 — ~140 ГБ. Тайлы в файле упорядочены по масштабу, поэтому «мир до z10» — это один сплошной
кусок в начале данных (~4 ГБ): качаем его одним диапазоном и пишем новое оглавление, смещения не меняются.
"""
from __future__ import annotations

import gzip
import math
import json
import time
from pathlib import Path

import httpx
from pmtiles.tile import Entry, deserialize_directory, deserialize_header
from pmtiles.writer import finalize_header

from .store import LIB_DIR, UA

MAPS_DIR = LIB_DIR / "maps"
BUILDS = "https://build-metadata.protomaps.dev/builds.json"


def latest_build() -> str:
    b = httpx.get(BUILDS, timeout=30, headers={"User-Agent": UA}).json()
    return "https://build.protomaps.com/" + b[-1]["key"]


class Remote:
    def __init__(self, url: str):
        self.url, self.c = url, httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": UA})

    def get(self, off: int, n: int) -> bytes:
        for i in range(5):
            try:
                r = self.c.get(self.url, headers={"Range": f"bytes={off}-{off + n - 1}"})
                if r.status_code == 206 and len(r.content) == n:
                    return r.content
            except httpx.HTTPError:
                pass
            time.sleep(3 * (i + 1))
        raise RuntimeError(f"не удалось прочитать {n} байт по смещению {off}")


def _limit_id(maxzoom: int) -> int:
    return sum(1 << (2 * z) for z in range(maxzoom + 1))


def collect_entries(rem: Remote, header: dict, maxzoom: int) -> list[Entry]:
    """Записи оглавления для всех тайлов z0..maxzoom (листовые каталоги читаем только нужные)."""
    limit = _limit_id(maxzoom)
    out: list[Entry] = []

    def walk(raw: bytes) -> bool:
        for e in deserialize_directory(raw):
            if e.tile_id >= limit:
                return False
            if e.run_length == 0:
                if not walk(rem.get(header["leaf_directory_offset"] + e.offset, e.length)):
                    return False
            else:
                run = min(e.run_length, limit - e.tile_id)
                out.append(Entry(e.tile_id, e.offset, e.length, run))
        return True

    walk(rem.get(header["root_offset"], header["root_length"]))
    return out


def _download_range(rem: Remote, start: int, length: int, dst: Path, progress=None) -> None:
    have = dst.stat().st_size if dst.exists() else 0
    with dst.open("ab") as f:
        while have < length:
            n = min(64 << 20, length - have)
            f.write(rem.get(start + have, n))
            have += n
            if progress:
                progress(have, length)


def extract_world(maxzoom: int = 10, out: Path | None = None, url: str | None = None, progress=None) -> Path:
    url = url or latest_build()
    rem = Remote(url)
    header = deserialize_header(rem.get(0, 127))
    if not header["clustered"]:
        raise RuntimeError("исходная карта не упорядочена — вырезка одним куском невозможна")
    meta = json.loads(gzip.decompress(rem.get(header["metadata_offset"], header["metadata_length"])))
    entries = collect_entries(rem, header, maxzoom)
    base = min(e.offset for e in entries)
    end = max(e.offset + e.length for e in entries)
    for e in entries:
        e.offset -= base

    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    out = out or MAPS_DIR / f"world_z{maxzoom}.pmtiles"
    part = out.with_suffix(".data.part")
    _download_range(rem, header["tile_data_offset"] + base, end - base, part, progress)

    addressed = sum(e.run_length for e in entries)
    contents = len({e.offset for e in entries})
    meta = dict(meta, maxzoom=maxzoom, source=url, extracted=time.strftime("%Y-%m-%d"))
    hdr = dict(header)
    hdr.update(max_zoom=maxzoom)
    hb, root, cmeta, leaves = finalize_header(hdr, addressed, entries, contents, meta, True, end - base)
    tmp = out.with_suffix(".tmp")
    with tmp.open("wb") as f:
        for b in (hb, root, cmeta, leaves):
            f.write(b)
        with part.open("rb") as d:
            while chunk := d.read(16 << 20):
                f.write(chunk)
    verify(tmp, rem, header, samples=200)
    tmp.replace(out)
    part.unlink(missing_ok=True)
    return out


def verify(path: Path, rem: Remote, src_header: dict, samples: int = 200, bbox=None, zr=None) -> None:
    """Сверка: случайные тайлы из вырезки побайтно совпадают с первоисточником."""
    import random
    from pmtiles.reader import MmapSource, Reader
    from pmtiles.tile import zxy_to_tileid, find_tile

    with path.open("rb") as f:
        rd = Reader(MmapSource(f))
        h = rd.header()
        src_root = deserialize_directory(rem.get(src_header["root_offset"], src_header["root_length"]))
        rnd = random.Random(1)
        checked = 0
        for _ in range(samples * 20):
            if checked >= samples:
                break
            if bbox and rnd.random() < 0.7:            # проверяем в основном вклеенный регион
                z = rnd.randint(*zr)
                x0, x1, y0, y1 = _tile_range(bbox, z)
                x, y = rnd.randint(x0, x1), rnd.randint(y0, y1)
            else:
                z = rnd.randint(0, h["max_zoom"])
                x, y = rnd.randrange(1 << z), rnd.randrange(1 << z)
            mine = rd.get(z, x, y)
            if mine is None:
                continue
            tid = zxy_to_tileid(z, x, y)
            d = src_root
            while True:
                e = find_tile(d, tid)
                if e is None:
                    raise RuntimeError(f"тайл {z}/{x}/{y} есть в вырезке, но нет в источнике")
                if e.run_length > 0:
                    break
                d = deserialize_directory(rem.get(src_header["leaf_directory_offset"] + e.offset, e.length))
            theirs = rem.get(src_header["tile_data_offset"] + e.offset, e.length)
            if theirs != mine:
                raise RuntimeError(f"тайл {z}/{x}/{y} не совпал с первоисточником")
            checked += 1
        if checked < samples // 2:
            raise RuntimeError(f"проверено слишком мало тайлов: {checked}")


if __name__ == "__main__":
    import sys
    z = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    t0 = time.time()

    def prog(done, total):
        print(f"  {done / 1e9:.2f} из {total / 1e9:.2f} ГБ, {time.time() - t0:.0f} с", flush=True)
    p = extract_world(z, progress=prog)
    print(f"ГОТОВО: {p} ({p.stat().st_size / 1e9:.2f} ГБ), 200 случайных тайлов совпали с первоисточником", flush=True)


# ─── подробный регион (z11..z15) поверх мира ──────────────────────────────

def _tile_range(bbox: tuple[float, float, float, float], z: int) -> tuple[int, int, int, int]:
    """bbox = (запад, юг, восток, север) → диапазон x, y тайлов на масштабе z."""
    w, s, e, n = bbox
    def tx(lon): return int((lon + 180) / 360 * (1 << z))
    def ty(lat):
        r = math.radians(lat)
        return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * (1 << z))
    return max(0, tx(w)), min((1 << z) - 1, tx(e)), max(0, ty(n)), min((1 << z) - 1, ty(s))


def region_ids(bbox, zmin: int, zmax: int) -> list[int]:
    from pmtiles.tile import zxy_to_tileid
    ids = []
    for z in range(zmin, zmax + 1):
        x0, x1, y0, y1 = _tile_range(bbox, z)
        ids.extend(zxy_to_tileid(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))
    ids.sort()
    return ids


def region_entries(rem: Remote, header: dict, ids: list[int]) -> list[Entry]:
    """Записи источника для нужных тайлов: читаем только те листовые каталоги, где они есть."""
    import bisect
    out: list[Entry] = []

    def walk(raw: bytes) -> None:
        d = deserialize_directory(raw)
        for k, e in enumerate(d):
            end = d[k + 1].tile_id if k + 1 < len(d) else 1 << 62
            if e.run_length == 0:
                i = bisect.bisect_left(ids, e.tile_id)
                if i < len(ids) and ids[i] < end:
                    walk(rem.get(header["leaf_directory_offset"] + e.offset, e.length))
            else:
                i = bisect.bisect_left(ids, e.tile_id)
                j = bisect.bisect_left(ids, e.tile_id + e.run_length)
                k2 = i
                while k2 < j:                          # подряд идущие нужные id внутри одного run — одной записью
                    s = k2
                    while k2 + 1 < j and ids[k2 + 1] == ids[k2] + 1:
                        k2 += 1
                    out.append(Entry(ids[s], e.offset, e.length, k2 - s + 1))
                    k2 += 1

    walk(rem.get(header["root_offset"], header["root_length"]))
    return out


def estimate_region(bbox, zmin: int = 11, zmax: int = 15, url: str | None = None) -> float:
    rem = Remote(url or latest_build())
    header = deserialize_header(rem.get(0, 127))
    ents = region_entries(rem, header, region_ids(bbox, zmin, zmax))
    return sum(ln for ln in {(e.offset, e.length) for e in ents} for ln in [ln[1]]) / 1e9


def _local_entries(path: Path) -> tuple[dict, dict, list[Entry]]:
    """Заголовок, метаданные и все записи локального PMTiles."""
    with path.open("rb") as f:
        def rd(o, n):
            f.seek(o)
            return f.read(n)
        h = deserialize_header(rd(0, 127))
        meta = json.loads(gzip.decompress(rd(h["metadata_offset"], h["metadata_length"])))
        out: list[Entry] = []

        def walk(raw):
            for e in deserialize_directory(raw):
                if e.run_length == 0:
                    walk(rd(h["leaf_directory_offset"] + e.offset, e.length))
                else:
                    out.append(e)
        walk(rd(h["root_offset"], h["root_length"]))
    return h, meta, out


def add_region(name: str, bbox, zmin: int = 11, zmax: int = 15, base: Path | None = None,
               out: Path | None = None, progress=None) -> Path:
    """Мир (base, z0..10) + подробный регион (zmin..zmax) из той же сборки → один файл карты."""
    from concurrent.futures import ThreadPoolExecutor
    base = base or MAPS_DIR / "world_z10.pmtiles"
    bh, bmeta, bents = _local_entries(base)
    url = bmeta.get("source") or latest_build()
    rem = Remote(url)
    sh = deserialize_header(rem.get(0, 127))
    ids = region_ids(bbox, zmin, zmax)
    rents = region_entries(rem, sh, ids)
    # уникальные куски данных по порядку → слитные диапазоны (дыры до 256 КБ скачиваем заодно)
    chunks = sorted({(e.offset, e.length) for e in rents})
    ranges, cur = [], None
    for o, n in chunks:
        if cur and o <= cur[1] + (256 << 10):
            cur[1] = max(cur[1], o + n)
        else:
            cur = [o, o + n]
            ranges.append(cur)
    part = MAPS_DIR / f"region_{name}.data.part"
    newoff: dict[int, int] = {}
    total = sum(b - a for a, b in ranges)
    done = 0
    with part.open("wb") as f, ThreadPoolExecutor(8) as ex:
        pos = 0
        batch = 64
        for i in range(0, len(ranges), batch):
            grp = ranges[i:i + batch]
            datas = list(ex.map(lambda r: rem.get(sh["tile_data_offset"] + r[0], r[1] - r[0]), grp))
            for (a, b), d in zip(grp, datas):
                for o, n in chunks_in(chunks, a, b):
                    newoff[o] = pos + (o - a)
                f.write(d)
                pos += len(d)
                done += len(d)
            if progress:
                progress(done, total)
    bdata = bh["tile_data_length"]
    region = [Entry(e.tile_id, bdata + newoff[e.offset], e.length, e.run_length) for e in rents]
    entries = sorted(bents + region, key=lambda e: e.tile_id)
    addressed = sum(e.run_length for e in entries)
    contents = len({(e.offset, e.length) for e in entries})
    regions = list(bmeta.get("regions", [])) + [{"name": name, "bbox": list(bbox), "zoom": [zmin, zmax]}]
    meta = dict(bmeta, maxzoom=zmax, regions=regions)
    hdr = dict(bh, max_zoom=zmax)
    hb, root, cmeta, leaves = finalize_header(hdr, addressed, entries, contents, meta, True, bdata + pos)
    out = out or MAPS_DIR / "world_map.pmtiles"
    tmp = out.with_suffix(".tmp")
    with tmp.open("wb") as f:
        for b in (hb, root, cmeta, leaves):
            f.write(b)
        with base.open("rb") as src:
            src.seek(bh["tile_data_offset"])
            left = bdata
            while left:
                c = src.read(min(16 << 20, left))
                f.write(c)
                left -= len(c)
        with part.open("rb") as d:
            while c := d.read(16 << 20):
                f.write(c)
    verify(tmp, rem, sh, samples=300, bbox=bbox, zr=(zmin, zmax))
    tmp.replace(out)
    part.unlink(missing_ok=True)
    return out


def chunks_in(chunks: list[tuple[int, int]], a: int, b: int):
    import bisect
    i = bisect.bisect_left(chunks, (a, 0))
    while i < len(chunks) and chunks[i][0] < b:
        yield chunks[i]
        i += 1
