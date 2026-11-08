"""System-tray app: runs the presence loop in the background with an icon next
to the clock showing what it's doing.

    pythonw -m tft_rpc tray      (no console window)

Icon colours: green = showing your TFT status, grey = waiting for TFT,
yellow = waiting for Discord, grey with bars = paused.
Right-click for: Pause, Start with Windows, Reload settings, Open log, Quit.
"""

import os
import subprocess
import sys
import threading
import time

from PIL import Image, ImageDraw

from . import startup
from .factory import DIAG_DIR, HERE, load_config
from .runner import ACTIVE, DISCORD_WAIT, IDLE, PAUSED, PresenceRunner

LOG_PATH = os.path.join(HERE, "tft_rpc.log")
MAX_LOG_BYTES = 1_000_000
COLOURS = {ACTIVE: (59, 165, 92), IDLE: (128, 132, 142), DISCORD_WAIT: (250, 166, 26),
           PAUSED: (128, 132, 142)}


# ---------- helpers ----------

def _single_instance():
    """Return a handle if we're the only tray instance, else None (Windows mutex)."""
    if sys.platform != "win32":
        return True
    import ctypes
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\tft-rpc-tray")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle


def _open_log():
    """Send print() output and errors to tft_rpc.log (pythonw has no console)."""
    try:
        if os.path.getsize(LOG_PATH) > MAX_LOG_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".old")
    except OSError:
        pass
    f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = f
    return f


def _log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}")


def _icon_image(kind):
    """A 64x64 round badge in the status colour (drawn here, no image files needed)."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 2, size - 2), fill=COLOURS.get(kind, COLOURS[IDLE]))
    d.ellipse((14, 14, size - 14, size - 14), outline=(255, 255, 255), width=5)
    if kind == PAUSED:
        d.rectangle((24, 22, 29, 42), fill=(255, 255, 255))
        d.rectangle((35, 22, 40, 42), fill=(255, 255, 255))
    else:
        d.ellipse((27, 27, 37, 37), fill=(255, 255, 255))
    return img


def _open_path(path):
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - opens with the default app
    else:
        subprocess.Popen(["xdg-open", path])


# ---------- tray app ----------

class TrayApp:
    def __init__(self):
        import pystray
        self.pystray = pystray
        self.status_text = "Starting…"
        self.status_kind = IDLE
        self.runner = None
        self.thread = None
        self.save_screenshots = None  # None until set; then kept across "Reload settings"
        self.icon = pystray.Icon(
            "tft-rpc", _icon_image(IDLE), "TFT Rich Presence", menu=self._menu())

    def _menu(self):
        M, Item = self.pystray.Menu, self.pystray.MenuItem
        return M(
            Item(lambda item: self.status_text, None, enabled=False),
            M.SEPARATOR,
            Item("Pause", self._toggle_pause, checked=lambda item: self._paused()),
            Item("Start with Windows", self._toggle_startup,
                 checked=lambda item: startup.is_installed()),
            Item("Save HP screenshots", self._toggle_screenshots,
                 checked=lambda item: self._saving()),
            Item("Reload settings", self._reload),
            Item("Open log", lambda icon, item: _open_path(LOG_PATH)),
            Item("Open screenshots folder", self._open_screenshots),
            Item("Open folder", lambda icon, item: _open_path(HERE)),
            M.SEPARATOR,
            Item("Quit", self._quit),
        )

    # runner lifecycle
    def _start_runner(self):
        try:
            cfg = load_config()
        except Exception as e:
            _log(f"Couldn't read config.json: {e!r}")
            self._on_status("config.json has an error (see log)", DISCORD_WAIT)
            return
        paused = self._paused()
        self.runner = PresenceRunner(cfg, log=_log, on_status=self._on_status)
        if paused:
            self.runner.paused.set()
        if self.save_screenshots is not None:
            self.runner.set_saving_screenshots(self.save_screenshots)
        self.thread = threading.Thread(target=self._run_safely, name="presence", daemon=True)
        self.thread.start()

    def _run_safely(self):
        try:
            self.runner.run()
        except Exception as e:
            _log(f"Presence loop crashed: {e!r}")
            self._on_status("Stopped after an error (see log)", DISCORD_WAIT)

    def _stop_runner(self):
        if self.runner:
            self.runner.stop()
        if self.thread:
            self.thread.join(timeout=10)

    def _paused(self):
        return bool(self.runner and self.runner.paused.is_set())

    def _on_status(self, text, kind):
        self.status_text, self.status_kind = text, kind
        try:
            self.icon.icon = _icon_image(kind)
            self.icon.title = f"TFT Rich Presence: {text}"[:120]  # Windows tooltip limit
            self.icon.update_menu()
        except Exception:
            pass

    # menu actions
    def _toggle_pause(self, icon, item):
        if not self.runner:
            return
        if self.runner.paused.is_set():
            self.runner.paused.clear()
        else:
            self.runner.paused.set()
        self.icon.update_menu()

    def _saving(self):
        return bool(self.runner and self.runner.saving_screenshots)

    def _toggle_screenshots(self, icon, item):
        if not self.runner:
            return
        self.save_screenshots = not self._saving()
        self.runner.set_saving_screenshots(self.save_screenshots)
        _log(f"HP screenshot saving {'on' if self.save_screenshots else 'off'}.")
        self.icon.update_menu()

    def _open_screenshots(self, icon, item):
        os.makedirs(DIAG_DIR, exist_ok=True)
        _open_path(DIAG_DIR)

    def _toggle_startup(self, icon, item):
        msg = startup.uninstall() if startup.is_installed() else startup.install()
        _log(msg)
        try:
            self.icon.notify(msg, "TFT Rich Presence")
        except Exception:
            pass
        self.icon.update_menu()

    def _reload(self, icon, item):
        _log("Reloading settings…")
        self._stop_runner()
        self._start_runner()

    def _quit(self, icon, item):
        self._stop_runner()
        self.icon.stop()

    def run(self):
        def setup(icon):
            icon.visible = True
            self._start_runner()
        self.icon.run(setup=setup)


def main():
    log_file = _open_log()
    lock = _single_instance()
    if not lock:
        _log("Already running in the tray; exiting this copy.")
        return
    _log("Tray app starting.")
    try:
        TrayApp().run()
    except ImportError:
        _log("pystray isn't installed. Run: python -m pip install pystray")
    finally:
        _log("Tray app exited.")
        log_file.flush()
