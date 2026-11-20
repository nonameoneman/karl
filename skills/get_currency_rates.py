"""Навык: курсы валют на сегодня (open.er-api.com, без ключа, раз в сутки).

Базовая валюта — местная: страна берётся из домашнего города («я живу в …» → «Город (KG)» → сом).
Город не известен — курсы к доллару США.
"""
import re

from jarvis.tools import tool

API = "https://open.er-api.com/v6/latest/"     # Rates By Exchange Rate API — https://www.exchangerate-api.com

# валюта → (кто, «чего» после числа)
_NAMES = {
    "USD": ("доллар", "долларов США"), "EUR": ("евро", "евро"), "RUB": ("рубль", "рублей"),
    "CNY": ("юань", "юаней"), "KZT": ("тенге", "тенге"), "KGS": ("сом", "сомов"), "UZS": ("сум", "сумов"),
    "TJS": ("сомони", "сомони"), "TMT": ("манат", "туркменских манатов"), "AZN": ("манат", "манатов"),
    "AMD": ("драм", "драмов"), "GEL": ("лари", "лари"), "BYN": ("белорусский рубль", "белорусских рублей"),
    "UAH": ("гривна", "гривен"), "MDL": ("лей", "леев"), "TRY": ("лира", "лир"), "GBP": ("фунт", "фунтов"),
    "JPY": ("иена", "иен"), "KRW": ("вона", "вон"), "INR": ("рупия", "рупий"), "AED": ("дирхам", "дирхамов"),
    "SAR": ("риял", "риялов"), "CHF": ("франк", "франков"), "PLN": ("злотый", "злотых"),
    "CZK": ("крона", "чешских крон"), "BRL": ("реал", "реалов"), "MXN": ("песо", "мексиканских песо"),
    "CAD": ("канадский доллар", "канадских долларов"), "AUD": ("австралийский доллар", "австралийских долларов"),
    "EGP": ("фунт", "египетских фунтов"), "ILS": ("шекель", "шекелей"), "THB": ("бат", "батов"),
}

_EURO = "AT BE CY DE EE ES FI FR GR HR IE IT LT LU LV MT NL PT SI SK ME XK AD MC SM VA".split()
_COUNTRY = {c: "EUR" for c in _EURO}
_COUNTRY.update({
    "US": "USD", "KG": "KGS", "KZ": "KZT", "UZ": "UZS", "TJ": "TJS", "TM": "TMT", "RU": "RUB", "BY": "BYN",
    "UA": "UAH", "MD": "MDL", "AZ": "AZN", "AM": "AMD", "GE": "GEL", "TR": "TRY", "CN": "CNY", "MN": "MNT",
    "GB": "GBP", "CH": "CHF", "NO": "NOK", "SE": "SEK", "DK": "DKK", "IS": "ISK", "PL": "PLN", "CZ": "CZK",
    "HU": "HUF", "RO": "RON", "BG": "BGN", "RS": "RSD", "BA": "BAM", "MK": "MKD", "AL": "ALL",
    "JP": "JPY", "KR": "KRW", "IN": "INR", "PK": "PKR", "BD": "BDT", "LK": "LKR", "NP": "NPR", "AF": "AFN",
    "IR": "IRR", "IQ": "IQD", "SA": "SAR", "AE": "AED", "QA": "QAR", "KW": "KWD", "BH": "BHD", "OM": "OMR",
    "JO": "JOD", "LB": "LBP", "SY": "SYP", "IL": "ILS", "EG": "EGP", "MA": "MAD", "DZ": "DZD", "TN": "TND",
    "LY": "LYD", "SD": "SDG", "NG": "NGN", "KE": "KES", "ET": "ETB", "ZA": "ZAR", "GH": "GHS", "TZ": "TZS",
    "TH": "THB", "VN": "VND", "ID": "IDR", "MY": "MYR", "SG": "SGD", "PH": "PHP", "HK": "HKD", "TW": "TWD",
    "AU": "AUD", "NZ": "NZD", "CA": "CAD", "MX": "MXN", "BR": "BRL", "AR": "ARS", "CL": "CLP", "CO": "COP",
    "PE": "PEN", "VE": "VES", "UY": "UYU", "BO": "BOB", "PY": "PYG", "CU": "CUP", "DO": "DOP",
    "PT": "EUR", "AO": "AOA", "MZ": "MZN", "CV": "CVE",
})


def home_currency() -> str:
    """Местная валюта по домашнему городу («Лос-Анджелес (US)» → USD); не знаем — USD."""
    from jarvis.config import settings
    m = re.search(r"\(([A-Z]{2})\)\s*$", settings.home_city or "")
    return _COUNTRY.get(m.group(1), "USD") if m else "USD"


def _fetch(base: str) -> tuple[str, dict[str, float]]:
    import httpx
    r = httpx.get(API + base, timeout=15, follow_redirects=True)
    r.raise_for_status()
    d = r.json()
    if d.get("result") != "success":
        raise RuntimeError(d.get("error-type", "нет данных"))
    return d["time_last_update_utc"][5:16], d["rates"]        # «20 Nov 2026», {код: сколько за 1 base}


def _num(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ") if v >= 0.1 else f"{v:.4f}"


@tool("green")
def get_currency_rates(currencies: str = "", base: str = "") -> str:
    """Курс валют на сегодня — «курс доллара», «почём евро», «сколько стоит юань в рублях».
    По умолчанию — в местной валюте пользователя.

    Args:
        currencies: коды валют через запятую (USD, EUR, RUB, CNY, KZT …); пусто — главные
        base: в какой валюте считать (RUB, KGS, USD …); пусто — местная валюта пользователя
    """
    base = (base or home_currency()).strip().upper()
    codes = [c.strip().upper() for c in currencies.split(",") if c.strip()] or \
        [c for c in ("USD", "EUR", "RUB", "CNY") if c != base][:3]
    try:
        date, rates = _fetch(base)
    except Exception as e:
        return f"Не удалось получить курсы валют: {e}"
    unit = _NAMES.get(base, (base, base))[1]
    parts = []
    for code in codes:
        if code == base:
            continue
        if not rates.get(code):
            parts.append(f"{code} — нет в курсах")
            continue
        parts.append(f"{_NAMES.get(code, (code,))[0]} — {_num(1 / rates[code])} {unit}")
    if not parts:
        return "Не указаны валюты."
    return f"Курс на {date}: " + ", ".join(parts) + "."


def test():
    res = get_currency_rates("EUR", "USD")
    assert res.startswith("Курс на") and "евро —" in res and "долларов США" in res, res
    rub = get_currency_rates("USD", "RUB")
    assert "доллар —" in rub and "рублей" in rub, rub
    return res + " | " + rub
