"""Голос: микрофон → энергетический VAD → Whisper → ключевое слово «Джарвис» → агент → ответ голосом (Piper)."""
from __future__ import annotations

import queue
import re
import threading
import time

import numpy as np

from . import bus
from .config import settings

SR = 16000
FRAME = 480                      # 30 мс
_HALLUCINATIONS = ("субтитр", "продолжение следует", "редактор субтитров", "спасибо за просмотр",
                   "подписывайтесь", "dimatorzok", "amara.org")
from . import lang as _lang
_YES, _NO = _lang.YES, _lang.NO                 # «да/нет» на всех языках Рассела


_STOP_WORDS = _lang.STOP


def _num_words(m: re.Match) -> str:
    from num2words import num2words
    s = m.group(0)
    try:
        if re.fullmatch(r"\d{1,2}:\d{2}", s):                      # время 02:33
            h, mi = s.split(":")
            return f"{num2words(int(h), lang='ru')} {num2words(int(mi), lang='ru') if int(mi) else 'ноль ноль'}"
        if re.fullmatch(r"\d+[.,]\d+", s):                         # 87.45 → восемьдесят семь и сорок пять
            a, b = re.split(r"[.,]", s)
            return f"{num2words(int(a), lang='ru')} и {num2words(int(b), lang='ru')}"
        return num2words(int(s), lang="ru")
    except Exception:
        return s


def _clean_for_speech(text: str) -> str:
    if _lang.cur() != "ru":                    # для других языков числа и знаки читает сам синтезатор
        t = re.sub(r"```.*?```", " ", text, flags=re.S)
        t = re.sub(r"https?://\S+", " ", t)
        t = re.sub(r"[*_#`>|\[\]]", "", t)
        return re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"```.*?```", " код в чате ", text, flags=re.S)
    t = re.sub(r"https?://\S+", "ссылка", t)
    t = re.sub(r"[*_#`>|\[\]]", "", t)
    t = t.replace("%", " процентов").replace("№", "номер ").replace("—", ", ").replace("ГБ", " гигабайт") \
         .replace("МБ", " мегабайт").replace("°C", " градусов")
    t = re.sub(r"(?<!\d)(\d{1,2}:\d{2}|\d+[.,]\d+|\d+)(?!\d)", _num_words, t)
    return re.sub(r"\s+", " ", t).strip()


class Speaker:
    """Озвучка Piper (офлайн, ONNX). Играет в браузере (там работает эхоподавление → можно перебивать)
    или в колонки через sounddevice. stop() обрывает речь мгновенно."""

    def __init__(self) -> None:
        self.q: queue.Queue[tuple[int, str]] = queue.Queue()
        self.gen = 0                       # поколение: stop() делает старые фразы недействительными
        self.playing_local = threading.Event()
        self.speaking_until = 0.0
        self.last_end = 0.0
        self.browser_out = lambda: False   # сервер подставляет: есть ли страница, которая играет звук
        self._voice = None
        self._voice_name = None
        threading.Thread(target=self._run, daemon=True, name="tts").start()

    def _load(self):
        from piper import PiperVoice
        from .config import MODELS_DIR
        name = _lang.voice_file()
        if self._voice is None or self._voice_name != name:
            self._voice = PiperVoice.load(MODELS_DIR / f"{name}.onnx")
            self._voice_name = name
        return self._voice

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        from piper.config import SynthesisConfig
        v = self._load()
        cfg = SynthesisConfig(length_scale=_lang.speed())
        chunks = list(v.synthesize(text, cfg))
        if not chunks:
            return np.zeros(0, np.int16), 22050
        return np.concatenate([c.audio_int16_array for c in chunks]), chunks[0].sample_rate

    def _run(self) -> None:
        try:
            self._load()
        except Exception as e:
            bus.publish({"type": "error", "text": f"TTS: {e}"})
        while True:
            gen, text = self.q.get()
            if gen != self.gen:
                continue
            try:
                audio, sr = self.synth(text)
            except Exception as e:
                bus.publish({"type": "error", "text": f"TTS: {e}"})
                continue
            if gen != self.gen or not len(audio):
                continue
            dur = len(audio) / sr
            if self.browser_out():
                import base64
                now = time.time()
                self.speaking_until = max(now, self.speaking_until) + dur
                bus.publish({"type": "audio", "sr": sr, "pcm": base64.b64encode(audio.tobytes()).decode()})
            else:
                import sounddevice as sd
                self.playing_local.set()
                try:
                    sd.play(audio, sr)
                    sd.wait()
                except Exception as e:
                    bus.publish({"type": "error", "text": f"Колонки: {e}"})
                self.playing_local.clear()
                self.last_end = time.time()

    def say(self, text: str) -> None:
        t = _clean_for_speech(text)
        if t:
            self.q.put((self.gen, t[:1500]))

    def stop(self) -> None:
        self.gen += 1
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        if self.playing_local.is_set():
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
        self.speaking_until = 0.0
        self.last_end = time.time()
        bus.publish({"type": "audio_stop"})

    def busy(self) -> bool:
        now = time.time()
        return (self.playing_local.is_set() or not self.q.empty() or now < self.speaking_until + 0.3
                or now - self.last_end < 0.3)


class Listener:
    def __init__(self, on_command, speaker: Speaker) -> None:
        self.on_command = on_command
        self.speaker = speaker
        self.model = None
        self.enabled = False
        self.follow_until = 0.0          # окно продолжения разговора без ключевого слова
        self.audio_q: queue.Queue[np.ndarray] = queue.Queue()
        self.state = "off"
        self.source = "system"
        self._asr_running = False
        self.confirm_hook = None         # () -> dict|None ; (cid, bool) -> None
        self._reset_seg()

    def _set_state(self, s: str) -> None:
        if s != self.state:
            self.state = s
            bus.publish({"type": "voice_state", "state": s})

    _load_lock = threading.Lock()

    def load(self) -> None:
        from faster_whisper import WhisperModel
        with self._load_lock:
            if self.model is not None:
                return
            self._load_model(WhisperModel)

    def _load_model(self, WhisperModel) -> None:
        try:
            self.model = WhisperModel(settings.whisper_model, device=settings.whisper_device, compute_type="int8_float16")
        except Exception:
            self.model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")

    def start(self, source: str = "system") -> None:
        """source: system — микрофон через Windows; browser — звук приходит из веб-страницы (feed)."""
        if self.enabled and self.source == source:
            return
        self.enabled = False
        time.sleep(0.1)
        self.enabled = True
        self.source = source
        self._reset_seg()
        if self.model is None:
            self._set_state("loading")
            threading.Thread(target=self._load_then_idle, daemon=True).start()
        else:
            self._set_state("idle")
        if source == "system":
            threading.Thread(target=self._capture, daemon=True, name="mic").start()
        if not self._asr_running:
            self._asr_running = True
            threading.Thread(target=self._process, daemon=True, name="asr").start()

    def _load_then_idle(self) -> None:
        self.load()
        if self.enabled and self.state == "loading":
            self._set_state("idle")

    def stop(self) -> None:
        self.enabled = False
        self._set_state("off")

    # ─── сегментация (общая для всех источников) ─────────────────────────
    def _reset_seg(self) -> None:
        self._noise = 0.004
        self._buf: list[np.ndarray] = []
        self._pre: list[np.ndarray] = []
        self._speech_frames = self._silence_frames = 0
        self._in_speech = False
        self._last_level = 0.0
        self._seg_during_tts = False

    def feed(self, frame: np.ndarray) -> None:
        """Кадр float32 16 кГц моно (любой длины ~20–40 мс)."""
        if not self.enabled or self.model is None:
            return
        talking = self.speaker.busy()
        if talking and self.source != "browser":   # без эхоподавления не слушаем сами себя
            self._buf.clear(); self._pre.clear(); self._in_speech = False; self._speech_frames = 0
            return
        rms = float(np.sqrt(np.mean(frame ** 2)) + 1e-9)
        now = time.time()
        if now - self._last_level > 0.25:
            self._last_level = now
            bus.publish({"type": "mic_level", "rms": round(rms, 5), "thr": round(max(self._noise * 2.8, 0.008), 5)})
        thr = max(self._noise * 2.8, 0.008)
        is_speech = rms > thr
        if not self._in_speech:
            self._noise = 0.97 * self._noise + 0.03 * min(rms, self._noise * 3)  # шумовой пол
            self._pre.append(frame)
            self._pre = self._pre[-10:]                                        # ~300 мс до начала речи
            self._speech_frames = self._speech_frames + 1 if is_speech else 0
            if self._speech_frames >= 4:
                self._in_speech, self._buf, self._silence_frames = True, self._pre[:], 0
                self._seg_during_tts = talking
                self._set_state("hearing")
        else:
            self._buf.append(frame)
            self._silence_frames = 0 if is_speech else self._silence_frames + 1
            n = sum(len(b) for b in self._buf)
            if self._silence_frames * len(frame) > 0.75 * SR or n > 15 * SR:  # 750 мс тишины или 15 с
                self.audio_q.put((np.concatenate(self._buf), self._seg_during_tts or talking))
                self._in_speech, self._buf, self._speech_frames = False, [], 0
                self._set_state("idle")

    def _capture(self) -> None:
        import sounddevice as sd
        try:
            with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=FRAME) as stream:
                while self.enabled and self.source == "system":
                    frame, _ = stream.read(FRAME)
                    self.feed(frame[:, 0].copy())
        except Exception as e:
            self.enabled = False
            self._set_state("mic_error")
            bus.publish({"type": "error", "text": "Windows не даёт открыть микрофон. Включи «Микрофон браузера» "
                                                  f"или разреши доступ в Параметры → Конфиденциальность → Микрофон. ({e})"})

    # ─── распознавание и маршрутизация ────────────────────────────────────
    def _transcribe(self, audio: np.ndarray) -> str:
        segs, _ = self.model.transcribe(audio, language=_lang.cur(), beam_size=1, vad_filter=True,
                                        initial_prompt=_lang.info()["prompt"], condition_on_previous_text=False)
        text = " ".join(s.text for s in segs if s.no_speech_prob < 0.7 and s.avg_logprob > -1.2).strip()
        if any(h in text.lower() for h in _HALLUCINATIONS):
            return ""
        return text

    def _process(self) -> None:
        while True:
            try:
                audio, during_tts = self.audio_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if len(audio) < SR * 0.35:
                continue
            self._set_state("transcribing")
            try:
                text = self._transcribe(audio)
            except Exception as e:
                bus.publish({"type": "error", "text": f"ASR: {e}"})
                text = ""
            self._set_state("idle" if self.enabled else "off")
            if not text:
                continue
            self._route(text, during_tts)

    def _route(self, text: str, during_tts: bool = False) -> None:
        low = text.lower().strip(" .,!?")
        words = re.findall(r"[\wё-]+", low)
        wake = _lang.find_wake(text, settings.wake_words)
        stop = bool(words) and len(words) <= 3 and any(w in _STOP_WORDS for w in words)
        # 0) перебивание: пока Russel говорит, реагируем только на имя и стоп-слова
        if during_tts or self.speaker.busy():
            if not (wake or stop):
                bus.publish({"type": "overheard", "text": text, "during_tts": True})
                return
            self.speaker.stop()
            bus.publish({"type": "interrupted", "text": text})
        if stop and not wake:
            bus.publish({"type": "heard", "text": text})
            self.follow_until = time.time() + 6
            return
        # 1) ждём подтверждения голосом
        pending = self.confirm_hook[0]() if self.confirm_hook else None
        if pending and pending.get("risk") == "yellow" and words:
            if any(w in _YES for w in words[:3]):
                bus.publish({"type": "heard", "text": text})
                self.confirm_hook[1](pending["id"], True)
                return
            if any(" ".join(words[:2]).startswith(n) for n in _NO):
                bus.publish({"type": "heard", "text": text})
                self.confirm_hook[1](pending["id"], False)
                return
        # 2) ключевое слово
        if wake:
            cmd = text[wake[1]:].strip(" ,.!?-—")
            bus.publish({"type": "heard", "text": text, "wake": True})
            if not cmd:
                self.speaker.say("Слушаю.")
                self.follow_until = time.time() + 9
                return
            self.follow_until = 0
            self.on_command(cmd)
            return
        # 3) продолжение разговора без ключевого слова
        if time.time() < self.follow_until and (len(words) >= 2 or (words and words[0] in _YES + _NO)):
            bus.publish({"type": "heard", "text": text})
            self.on_command(text)
            return
        bus.publish({"type": "overheard", "text": text})

    def open_followup(self, seconds: float | None = None) -> None:
        """Окно «продолжения» отсчитывается от момента, когда Russel договорил."""
        def _wait():
            time.sleep(0.5)
            while self.speaker.busy():
                time.sleep(0.1)
            self.follow_until = time.time() + (seconds or settings.followup_seconds)
        threading.Thread(target=_wait, daemon=True).start()
