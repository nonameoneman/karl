"""Языки Рассела: шесть официальных языков ООН + хинди и португальский (~5–6 млрд человек)."""
from __future__ import annotations

import re

from .config import settings

LANGS: dict[str, dict] = {
    "ru": {"native": "Русский", "name": "Рассел", "voice": "ru_RU-{tts}-medium", "speed": None,
           "prompt": "Голосовые команды ассистенту по имени Рассел.",
           "greet": "Привет, {user}. Я Рассел: разумный автономный самообучающийся собеседник — на случай, если ляжет интернет. Просто скажите «Рассел» — и что нужно.",
           "reply": "русском"},
    "en": {"native": "English", "name": "Russel", "voice": "en_GB-alan-medium", "speed": 1.0,
           "prompt": "Voice commands to an assistant named Russel.",
           "greet": "Hello, {user}. I am Russel: reliable unplugged self-learning sovereign edge logic. Just say “Russel” and what you need.",
           "reply": "английском (English)"},
    "zh": {"native": "中文", "name": "拉塞尔", "voice": "zh_CN-chaowen-medium", "speed": 1.0,
           "prompt": "对名叫拉塞尔的助手的语音指令。",
           "greet": "你好，{user}。我是拉塞尔：可靠、离线、自学、自主的本地逻辑。只要说“拉塞尔”，再说你需要什么。",
           "reply": "китайском (中文, путунхуа; иероглифы — как пишет пользователь)"},
    "es": {"native": "Español", "name": "Russel", "voice": "es_ES-davefx-medium", "speed": 1.0,
           "prompt": "Comandos de voz para un asistente llamado Russel.",
           "greet": "Hola, {user}. Soy Russel: lógica local fiable, sin conexión, autodidacta y soberana. Solo di «Russel» y lo que necesitas.",
           "reply": "испанском (Español)"},
    "ar": {"native": "العربية", "name": "راسل", "voice": "ar_JO-kareem-medium", "speed": 1.0,
           "prompt": "أوامر صوتية لمساعد اسمه راسل.",
           "greet": "مرحبًا يا {user}. أنا راسل: منطق محلي موثوق يعمل دون إنترنت، يتعلم ذاتيًا ومستقل. فقط قل «راسل» وما تحتاجه.",
           "reply": "арабском (литературный арабский, العربية الفصحى)"},
    "fr": {"native": "Français", "name": "Russel", "voice": "fr_FR-tom-medium", "speed": 1.0,
           "prompt": "Commandes vocales pour un assistant nommé Russel.",
           "greet": "Bonjour, {user}. Je suis Russel : une logique locale fiable, hors ligne, autodidacte et souveraine. Dites simplement « Russel » et ce qu'il vous faut.",
           "reply": "французском (Français)"},
    "hi": {"native": "हिन्दी", "name": "रसेल", "voice": "hi_IN-rohan-medium", "speed": 1.0,
           "prompt": "रसेल नाम के सहायक के लिए आवाज़ आदेश।",
           "greet": "नमस्ते, {user}। मैं रसेल हूँ: भरोसेमंद, ऑफ़लाइन, स्व-शिक्षित और स्वतंत्र स्थानीय तर्क। बस “रसेल” कहिए और बताइए क्या चाहिए।",
           "reply": "хинди (हिन्दी)"},
    "pt": {"native": "Português", "name": "Russel", "voice": "pt_BR-faber-medium", "speed": 1.0,
           "prompt": "Comandos de voz para um assistente chamado Russel.",
           "greet": "Olá, {user}. Eu sou o Russel: lógica local confiável, offline, autodidata e soberana. É só dizer “Russel” e o que você precisa.",
           "reply": "португальском (Português)"},
}

# имя Рассела во всех языках — откликается на любое, на каком бы языке ни говорили.
# Только целым словом в начале фразы (см. find_wake): «расскажи», «на русском» не будят.
ALL_NAMES = ["рассел", "расел", "руссел", "русел", "расселл", "рас", "расс", "рус", "русс", "русси", "расси", "рассик",
             "russel", "russell", "russ", "rus", "拉塞尔", "拉塞爾", "罗素", "راسل", "رسل", "रसेल", "रसल"]
YES = ("да", "давай", "подтверждаю", "выполняй", "можно", "ок", "окей", "конечно", "ага", "угу", "делай",
       "yes", "yeah", "sure", "ok", "okay", "sí", "si", "claro", "oui", "sim", "是", "好", "好的", "可以", "对",
       "نعم", "أجل", "حسنا", "हाँ", "हां", "जी", "ठीक")
NO = ("нет", "не надо", "отмена", "отмени", "стоп", "не нужно", "no", "nope", "cancel", "non", "não", "nao",
      "不", "不要", "取消", "لا", "إلغاء", "नहीं", "नही", "रद्द")
STOP = ("стоп", "хватит", "замолчи", "помолчи", "тихо", "достаточно", "заткнись", "остановись",
        "stop", "enough", "quiet", "basta", "para", "silencio", "arrête", "assez", "chut", "pare", "chega",
        "停", "停止", "别说了", "安静", "توقف", "كفى", "اسكت", "रुको", "बस", "चुप")


_PLAIN = re.compile(r"^[a-zа-яё]+$")       # кириллица и латиница — сравниваем целыми словами


def find_wake(text: str, extra: list[str] | tuple = ()) -> tuple[int, int] | None:
    """Где в фразе обращение по имени: (начало, конец) или None.

    Имя должно стоять целым словом среди первых трёх слов («Рассел, …», «Эй, Рас, …»); «расскажи», «расписание»,
    «на русском» и «тот самый Рассел» в середине длинной фразы ассистента не будят."""
    low = text.lower()
    names = sorted({n.lower() for n in list(extra) + ALL_NAMES if n}, key=len, reverse=True)
    for n in names:
        if not _PLAIN.match(n):                 # китайский, арабский, хинди — имя в первых символах фразы
            i = low.find(n)
            if 0 <= i <= 8:
                return i, i + len(n)
    for k, m in enumerate(re.finditer(r"[\wё-]+", low)):
        if k >= 3:
            break
        if m.group(0).strip("-") in names:
            return m.start(), m.end()
    return None


def cur() -> str:
    return settings.language if settings.language in LANGS else "ru"


def greeting(user: str = "") -> str:
    """Приветствие; без имени — «Привет. Я Рассел…» вместо «Привет, .»."""
    import re
    g = info()["greet"]
    return g.format(user=user) if user else re.sub(r"[,，،]?\s*(يا\s*)?\{user\}", "", g)


def info(code: str | None = None) -> dict:
    return LANGS[code or cur()]


def voice_file() -> str:
    v = info()["voice"]
    return v.format(tts=settings.tts_voice) if "{tts}" in v else v


def speed() -> float:
    s = info()["speed"]
    return settings.tts_speed if s is None else s
