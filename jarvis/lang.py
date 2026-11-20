"""Языки Карла: шесть официальных языков ООН + хинди и португальский (~5–6 млрд человек)."""
from __future__ import annotations

from .config import settings

LANGS: dict[str, dict] = {
    "ru": {"native": "Русский", "name": "Карл", "voice": "ru_RU-{tts}-medium", "speed": None,
           "prompt": "Голосовые команды ассистенту по имени Карл.",
           "greet": "Привет, {user}. Я Карл: комплексная автономная резервная логика. Просто скажите «Карл» — и что нужно.",
           "reply": "русском"},
    "en": {"native": "English", "name": "Carl", "voice": "en_GB-alan-medium", "speed": 1.0,
           "prompt": "Voice commands to an assistant named Carl.",
           "greet": "Hello, {user}. I am Carl: comprehensive autonomous reserve logic. Just say “Carl” and what you need.",
           "reply": "английском (English)"},
    "zh": {"native": "中文", "name": "卡尔", "voice": "zh_CN-chaowen-medium", "speed": 1.0,
           "prompt": "对名叫卡尔的助手的语音指令。",
           "greet": "你好，{user}。我是卡尔：综合自主备用逻辑。只要说“卡尔”，再说你需要什么。",
           "reply": "китайском (中文, путунхуа; иероглифы — как пишет пользователь)"},
    "es": {"native": "Español", "name": "Carl", "voice": "es_ES-davefx-medium", "speed": 1.0,
           "prompt": "Comandos de voz para un asistente llamado Carl.",
           "greet": "Hola, {user}. Soy Carl: cerebro autónomo de respaldo local. Solo di «Carl» y lo que necesitas.",
           "reply": "испанском (Español)"},
    "ar": {"native": "العربية", "name": "كارل", "voice": "ar_JO-kareem-medium", "speed": 1.0,
           "prompt": "أوامر صوتية لمساعد اسمه كارل.",
           "greet": "مرحبًا يا {user}. أنا كارل: منطق احتياطي مستقل شامل. فقط قل «كارل» وما تحتاجه.",
           "reply": "арабском (литературный арабский, العربية الفصحى)"},
    "fr": {"native": "Français", "name": "Carl", "voice": "fr_FR-tom-medium", "speed": 1.0,
           "prompt": "Commandes vocales pour un assistant nommé Carl.",
           "greet": "Bonjour, {user}. Je suis Carl : cerveau autonome de réserve logique. Dites simplement « Carl » et ce qu'il vous faut.",
           "reply": "французском (Français)"},
    "hi": {"native": "हिन्दी", "name": "कार्ल", "voice": "hi_IN-rohan-medium", "speed": 1.0,
           "prompt": "कार्ल नाम के सहायक के लिए आवाज़ आदेश।",
           "greet": "नमस्ते, {user}। मैं कार्ल हूँ: व्यापक स्वायत्त आरक्षित तर्क। बस “कार्ल” कहिए और बताइए क्या चाहिए।",
           "reply": "хинди (हिन्दी)"},
    "pt": {"native": "Português", "name": "Carl", "voice": "pt_BR-faber-medium", "speed": 1.0,
           "prompt": "Comandos de voz para um assistente chamado Carl.",
           "greet": "Olá, {user}. Eu sou o Carl: cérebro autônomo de reserva lógica. É só dizer “Carl” e o que você precisa.",
           "reply": "португальском (Português)"},
}

# имя Карла во всех языках — откликается на любое, на каком бы языке ни говорили
ALL_NAMES = ["карл", "корл", "carl", "karl", "卡尔", "卡爾", "كارل", "कार्ल"]
YES = ("да", "давай", "подтверждаю", "выполняй", "можно", "ок", "окей", "конечно", "ага", "угу", "делай",
       "yes", "yeah", "sure", "ok", "okay", "sí", "si", "claro", "oui", "sim", "是", "好", "好的", "可以", "对",
       "نعم", "أجل", "حسنا", "हाँ", "हां", "जी", "ठीक")
NO = ("нет", "не надо", "отмена", "отмени", "стоп", "не нужно", "no", "nope", "cancel", "non", "não", "nao",
      "不", "不要", "取消", "لا", "إلغاء", "नहीं", "नही", "रद्द")
STOP = ("стоп", "хватит", "замолчи", "помолчи", "тихо", "достаточно", "заткнись", "остановись",
        "stop", "enough", "quiet", "basta", "para", "silencio", "arrête", "assez", "chut", "pare", "chega",
        "停", "停止", "别说了", "安静", "توقف", "كفى", "اسكت", "रुको", "बस", "चुप")


def cur() -> str:
    return settings.language if settings.language in LANGS else "ru"


def greeting(user: str = "") -> str:
    """Приветствие; без имени — «Привет. Я Карл…» вместо «Привет, .»."""
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
