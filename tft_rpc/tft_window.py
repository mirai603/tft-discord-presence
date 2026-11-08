"""Finds the TFT game window on Windows so OCR can read from it in any display
mode: windowed, borderless or fullscreen. Returns the window's drawable area
(without the title bar and borders) in physical screen pixels."""

import sys
import time

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    # Use real pixel coordinates even with Windows display scaling (e.g. 125%),
    # so window positions line up with the screenshots.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_EnumProc, wintypes.LPARAM]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.GetForegroundWindow.restype = wintypes.HWND


class TFTWindow:
    PROCESS_PREFIX = "tftclient"  # TFTClient.exe / TFTClient-Win64-Shipping.exe

    def __init__(self):
        self._pids = set()
        self._pids_checked = 0.0
        self.hwnd = None

    def _tft_pids(self):
        if time.time() - self._pids_checked > 10:
            self._pids_checked = time.time()
            try:
                import psutil
                self._pids = {p.pid for p in psutil.process_iter(["name"])
                              if (p.info["name"] or "").lower().startswith(self.PROCESS_PREFIX)}
            except ImportError:
                self._pids = set()
        return self._pids

    def find(self):
        """Return {'left','top','width','height','minimized','focused'} or None."""
        if not _IS_WINDOWS:
            return None
        pids = self._tft_pids()
        if not pids:
            return None
        best = []

        def callback(hwnd, _lparam):
            if not _user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                rect = wintypes.RECT()
                _user32.GetClientRect(hwnd, ctypes.byref(rect))
                best.append((rect.right * rect.bottom, hwnd, rect))
            return True

        _user32.EnumWindows(_EnumProc(callback), 0)
        if not best:
            return None
        _area, hwnd, rect = max(best, key=lambda b: b[0])  # the game view, not a splash/popup
        self.hwnd = hwnd
        origin = wintypes.POINT(0, 0)
        _user32.ClientToScreen(hwnd, ctypes.byref(origin))
        return {
            "left": origin.x,
            "top": origin.y,
            "width": rect.right,
            "height": rect.bottom,
            "minimized": bool(_user32.IsIconic(hwnd)) or rect.right == 0,
            "focused": _user32.GetForegroundWindow() == hwnd,
        }


def region_box(area, region, anchor="center"):
    """Map a region measured on a 16:9 screen onto a game area of any size.

    TFT's UI scales with the window's height, and each element is pinned to an
    edge (the stage banner to the top centre, the player list to the right).
    For 16:9 windows this is the same as plain fractions; for other shapes it
    keeps the box on the element instead of drifting sideways.
    """
    fl, ft, fw, fh = region
    W, H = area["width"], area["height"]
    ref_w = H * 16 / 9  # width of the 16:9 layout at this height
    if anchor == "right":
        x = W - (1 - fl) * ref_w
    elif anchor == "left":
        x = fl * ref_w
    else:  # centre
        x = W / 2 + (fl - 0.5) * ref_w
    return {
        "left": area["left"] + int(x),
        "top": area["top"] + int(ft * H),
        "width": max(1, int(fw * ref_w)),
        "height": max(1, int(fh * H)),
    }
