"""Настройки Jarvis. Переопределяются через data/settings.json."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "jarvis"
SKILLS_DIR = ROOT / "skills"
DATA_DIR = Path(os.environ["JARVIS_DATA_DIR"]) if os.environ.get("JARVIS_DATA_DIR") else ROOT / "data"
MODELS_DIR = ROOT / "data" / "models"
BACKUP_DIR = DATA_DIR / "backups"
SETTINGS_FILE = DATA_DIR / "settings.json"


def _paths() -> dict:
    """Пути этого компьютера: data/paths.json (не в git) → переменные окружения → значения по умолчанию."""
    try:
        return json.loads((ROOT / "data" / "paths.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_P = _paths()
_portable = ROOT.parent / "ollama" / "ollama.exe"          # портативная Ollama рядом с папкой проекта
OLLAMA_EXE = Path(_P.get("ollama_exe") or os.environ.get("JARVIS_OLLAMA_EXE")
                  or (str(_portable) if _portable.exists() else shutil.which("ollama") or "ollama"))
OLLAMA_MODELS = _P.get("ollama_models") or os.environ.get("OLLAMA_MODELS", "")  # пусто — папка Ollama по умолчанию
LIBRARY_DIR = Path(_P.get("library") or os.environ.get("JARVIS_LIBRARY") or Path.home() / "KarlLibrary")

for _d in (SKILLS_DIR, DATA_DIR, BACKUP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

RESTART_EXIT_CODE = 3


@dataclass
class Settings:
    model: str = "gemma4:e4b"
    num_ctx: int = 8192
    temperature: float = 0.4
    think: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    user_name: str = ""               # как обращаться к пользователю; пусто — без имени
    assistant_name: str = "Карл"
    wake_words: list[str] = field(default_factory=lambda: [
        "карл", "karl", "carl", "корл"])
    voice_enabled: bool = True
    voice_autostart: bool = True
    followup_seconds: int = 30        # сколько после ответа слушаю без обращения по имени
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"
    tts_voice: str = "dmitri"         # dmitri | ruslan | denis | irina (Piper)
    tts_speed: float = 0.85           # length_scale: меньше — быстрее
    max_steps: int = 12
    always_allow: list[str] = field(default_factory=list)   # инструменты 🟡 без подтверждения
    contact_email: str = ""           # контакт для User-Agent там, где сервис его требует (Wikimedia)
    language: str = "ru"              # язык интерфейса и разговора: ru en zh es ar fr hi pt
    home_city: str = ""               # «где я» для поиска мест рядом (офлайн-карты)
    home_lat: float = 0.0
    home_lon: float = 0.0

    def save(self) -> None:
        SETTINGS_FILE.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


def load_settings() -> Settings:
    s = Settings()
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(s, k):
                    setattr(s, k, v)
        except Exception:
            pass
    return s


settings = load_settings()
