"""Russel как обычная программа: своё окно (без браузера), значок у часов, один экземпляр.

Запуск: pythonw -m russel.desktop
Окно — системный движок (Windows: WebView2, macOS: WKWebView). Ядро Russel работает отдельным процессом;
окно его запускает, перезапускает после самообновления (код 3) и после сбоя.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parents[1]
HOST, PORT, LOCK_PORT = "127.0.0.1", 8765, 8764
URL = f"http://{HOST}:{PORT}/"
WIN = sys.platform == "win32"
MAC = sys.platform == "darwin"

SPLASH = """<!doctype html><html><body style="margin:0;height:100vh;display:grid;place-items:center;background:#07080c;
font-family:'Segoe UI',-apple-system,sans-serif;color:#7d8594"><div style="text-align:center">
<div style="width:120px;height:120px;margin:0 auto 26px;border-radius:50%;background:radial-gradient(circle,#bfeaff 0,#3aa8e0 35%,
rgba(60,120,255,.15) 65%,transparent 72%);animation:b 2.4s ease-in-out infinite"></div><div id="s">Просыпаюсь…</div></div>
<style>@keyframes b{50%{transform:scale(1.12);opacity:.7}}</style></body></html>"""


def _alive() -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/state", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


class Core:
    """Ядро Russel в отдельном процессе под присмотром."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.owned = False
        self.quitting = False

    def start(self) -> None:
        if _alive():                       # уже запущено (например, из Russel.bat) — просто подключаемся
            return
        self.owned = True
        threading.Thread(target=self._supervise, daemon=True, name="core").start()

    def _spawn(self) -> subprocess.Popen:
        py = Path(sys.executable)
        if py.name.lower() == "pythonw.exe":
            py = py.with_name("python.exe")
        env = dict(os.environ, PYTHONUTF8="1")
        log = (ROOT / "data").joinpath("russel.log")
        log.parent.mkdir(exist_ok=True)
        flags = subprocess.CREATE_NO_WINDOW if WIN else 0
        return subprocess.Popen([str(py), "-m", "russel", "--no-browser"], cwd=ROOT, env=env,
                                stdout=log.open("a", encoding="utf-8"), stderr=subprocess.STDOUT, creationflags=flags)

    def _supervise(self) -> None:
        fails: list[float] = []
        while not self.quitting:
            self.proc = self._spawn()
            code = self.proc.wait()
            if self.quitting:
                break
            if code != 3:                  # 3 — самообновление, остальное — сбой
                fails = [t for t in fails if time.time() - t < 120] + [time.time()]
                if len(fails) > 4:
                    break                  # падает раз за разом — не крутимся бесконечно
                time.sleep(2)

    def stop(self) -> None:
        self.quitting = True
        if self.owned and self.proc and self.proc.poll() is None:
            self.proc.terminate()


def _single_instance(on_show) -> bool:
    """True — мы первые. Иначе просим уже открытое окно показаться и выходим."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((HOST, LOCK_PORT))
    except OSError:
        try:
            with socket.create_connection((HOST, LOCK_PORT), timeout=2) as c:
                c.sendall(b"show")
        except OSError:
            pass
        return False
    s.listen(4)

    def serve():
        while True:
            c, _ = s.accept()
            with c:
                if c.recv(16) == b"show":
                    on_show()
    threading.Thread(target=serve, daemon=True, name="instance").start()
    return True


def _allow_microphone() -> None:
    """WebView2 по умолчанию спрашивает про микрофон своим окошком — для своей страницы разрешаем сами."""
    if not WIN:
        return
    from webview.platforms import edgechromium as ec
    orig = ec.EdgeChrome.on_webview_ready

    def ready(self, sender, args):
        orig(self, sender, args)
        if not args.IsSuccess:
            return
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionKind, CoreWebView2PermissionState

        def on_perm(_s, a):
            if a.Uri.startswith(f"http://{HOST}:{PORT}") and a.PermissionKind in (
                    CoreWebView2PermissionKind.Microphone, CoreWebView2PermissionKind.Autoplay):
                a.State = CoreWebView2PermissionState.Allow
        sender.CoreWebView2.PermissionRequested += on_perm
    ec.EdgeChrome.on_webview_ready = ready


def _dark_titlebar(window) -> None:
    if not WIN:
        return
    try:
        import ctypes
        hwnd = int(str(window.native.Handle))
        val = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
        for attr, color in ((35, 0x000C0807), (34, 0x000C0807), (36, 0x00F6F1EE)):   # заголовок и рамка — в цвет окна
            c = ctypes.c_int(color)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(c), ctypes.sizeof(c))
    except Exception:
        pass


def _icon_image():
    from PIL import Image, ImageDraw, ImageFilter
    n = 256
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for r, col in ((118, (40, 110, 230, 255)), (96, (58, 168, 224, 255)), (62, (140, 220, 255, 255)), (30, (230, 248, 255, 255))):
        d.ellipse((n / 2 - r, n / 2 - r, n / 2 + r, n / 2 + r), fill=col)
    return img.filter(ImageFilter.GaussianBlur(6))


def ensure_icon() -> Path:
    ico = ROOT / "data" / "russel.ico"
    if not ico.exists():
        ico.parent.mkdir(exist_ok=True)
        _icon_image().save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ico


def main() -> None:
    core = Core()
    window = None
    state = {"hidden": False, "told": False, "tray": None}

    def show():
        if window:
            window.show()
            window.restore()
            state["hidden"] = False

    if not _single_instance(show):
        return

    _allow_microphone()
    window = webview.create_window("Рассел", html=SPLASH, width=480, height=780, min_size=(360, 520),
                                   background_color="#07080C", text_select=True)

    def quit_all(*_):
        core.stop()
        if state["tray"]:
            state["tray"].stop()
        state["quit"] = True
        window.destroy()

    def on_closing():
        if state.get("quit") or not state["tray"]:
            core.stop()
            return True
        window.hide()                      # крестик прячет в угол экрана — Russel продолжает слушать
        state["hidden"] = True
        if not state["told"]:
            state["told"] = True
            try:
                state["tray"].notify("Я продолжаю слушать. Значок — у часов, внизу справа.", "Рассел здесь")
            except Exception:
                pass
        return False

    window.events.closing += on_closing
    window.events.shown += lambda: _dark_titlebar(window)

    if WIN:
        try:
            import pystray
            menu = pystray.Menu(pystray.MenuItem("Открыть Рассела", lambda *_: show(), default=True),
                                pystray.MenuItem("Выйти", quit_all))
            state["tray"] = pystray.Icon("russel", _icon_image(), "Рассел", menu)
            state["tray"].run_detached()
        except Exception:
            state["tray"] = None

    def boot():
        core.start()
        t0 = time.time()
        while not _alive():
            if time.time() - t0 > 180:
                window.evaluate_js("document.getElementById('s').textContent="
                                   "'Не получается запуститься. Подробности — в data/russel.log'")
                return
            time.sleep(0.5)
        window.load_url(URL)

    data = ROOT / "data" / "webview"
    data.mkdir(parents=True, exist_ok=True)
    webview.start(boot, private_mode=False, storage_path=str(data), icon=str(ensure_icon()))
    core.stop()
    if state["tray"]:
        try:
            state["tray"].stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
