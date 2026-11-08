"""Reads the stage indicator (e.g. "3-2") from the top of the TFT screen with OCR.

Riot's local APIs don't expose the current stage, so this is the only way to
get it without a third-party overlay platform. It only takes screenshots of
one small region of your screen, the same way OBS or a screen recorder does.

Needs: pip install mss pillow pytesseract, plus the Tesseract OCR program
(Windows installer: https://github.com/UB-Mannheim/tesseract/wiki).
"""

import re

from .tft_window import TFTWindow, region_box

STAGE_RE = re.compile(r"([1-9])\s*[-–—]\s*([1-7])")

try:
    import mss
    from PIL import Image, ImageFilter, ImageOps
    import pytesseract
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


class StageReader:
    def __init__(self, region, monitor=1, tesseract_cmd=None, confirm_reads=2,
                 capture="window", only_when_focused=True, anchor="center"):
        """region: [left, top, width, height] as fractions of a 16:9 game view.

        capture="window" reads from the TFT window wherever it is (windowed,
        borderless or fullscreen) and falls back to the monitor if the window
        can't be found; capture="monitor" always uses the whole monitor.
        """
        self.region = region
        self.monitor = monitor
        self.capture = capture
        self.only_when_focused = only_when_focused
        self.anchor = anchor
        self.window = TFTWindow()
        self.last_source = None
        self.confirm_reads = confirm_reads
        self._candidate = None
        self._count = 0
        self.stage = None
        if AVAILABLE and tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    def _area(self, sct):
        """The game's drawable area in screen pixels, or None to skip this read."""
        if self.capture == "window":
            win = self.window.find()
            if win:
                if win["minimized"]:
                    return None
                if self.only_when_focused and not win["focused"]:
                    return None  # another window may be covering the game
                self.last_source = f"TFT window {win['width']}x{win['height']} at ({win['left']},{win['top']})"
                return win
        mon = sct.monitors[self.monitor]
        self.last_source = f"monitor {self.monitor} {mon['width']}x{mon['height']}"
        return mon

    def _grab(self):
        with mss.mss() as sct:
            area = self._area(sct)
            if area is None:
                return None
            shot = sct.grab(region_box(area, self.region, self.anchor))
            return Image.frombytes("RGB", shot.size, shot.rgb)

    @staticmethod
    def _prep(img):
        # Light text on a dark banner: grayscale, upscale, threshold, invert.
        g = ImageOps.grayscale(img)
        g = g.resize((g.width * 3, g.height * 3), Image.LANCZOS)
        g = g.point(lambda p: 255 if p > 150 else 0)
        return ImageOps.invert(g)

    def read_once(self):
        raw = self._grab()
        if raw is None:
            return None
        img = self._prep(raw)
        text = pytesseract.image_to_string(
            img, config="--psm 7 -c tessedit_char_whitelist=0123456789-"
        )
        m = STAGE_RE.search(text)
        return f"{m.group(1)}-{m.group(2)}" if m else None

    def update(self):
        """Read the screen; only accept a new stage after N identical reads in a row
        so a single bad OCR frame doesn't flicker your Discord status."""
        if not AVAILABLE:
            return self.stage
        try:
            reading = self.read_once()
        except Exception:
            return self.stage
        if reading is None:
            return self.stage
        if reading == self._candidate:
            self._count += 1
        else:
            self._candidate, self._count = reading, 1
        if self._count >= self.confirm_reads:
            self.stage = reading
        return self.stage

    def reset(self):
        self.stage, self._candidate, self._count = None, None, 0

    def save_debug_images(self, prefix="calibrate"):
        """Save the full screen with the region outlined, plus the processed crop."""
        from PIL import ImageDraw
        saved_focus, self.only_when_focused = self.only_when_focused, False
        with mss.mss() as sct:
            area = self._area(sct) or sct.monitors[self.monitor]
            shot = sct.grab({k: area[k] for k in ("left", "top", "width", "height")})
            full = Image.frombytes("RGB", shot.size, shot.rgb)
        self.only_when_focused = saved_focus
        r = region_box(area, self.region, self.anchor)
        box = (r["left"] - area["left"], r["top"] - area["top"],
               r["left"] - area["left"] + r["width"], r["top"] - area["top"] + r["height"])
        crop = full.crop(box)
        ImageDraw.Draw(full).rectangle(box, outline=(255, 0, 0), width=3)
        full.save(f"{prefix}_full.png")
        self._prep(crop).save(f"{prefix}_crop.png")
        return f"{prefix}_full.png", f"{prefix}_crop.png"


# ---------------------------------------------------------------------------
# Your HP, from the player list on the right. Your own row is the enlarged one
# with a gold ring around the portrait (everyone else has red), and it moves up
# and down as the list re-sorts by HP, so we find the ring and read the number
# in the box just left of it. Measurements are from a 2560x1440 screenshot.
# ---------------------------------------------------------------------------

HP_STRIP = [0.89, 0.08, 0.11, 0.78]   # 16:9 fractions, pinned to the right edge
REF_H = 1440


def _is_gold(px):
    r, g, b = px[:3]
    return r > 175 and g > 140 and b < 125 and r - b > 85


def find_ring(strip, scale):
    """Centre (x, y) of your gold portrait ring in strip pixels, or None.

    Finds a tall run of rows containing gold (small gaps allowed), takes its
    TOP edge (the star badge can extend the bottom), and gets the horizontal
    centre from the ring's top arc. The ring may be partly cut off by the
    window's right edge, which is fine.
    """
    W, H = strip.size
    px = strip.load()
    x0 = int(W * 0.5)
    gold_xs = [[x for x in range(x0, W) if _is_gold(px[x, y])] for y in range(H)]
    has_gold = [len(xs) >= 2 for xs in gold_xs]
    min_run, max_gap = max(10, int(55 * scale)), max(2, int(3 * scale))
    best, start, gap = None, None, 0
    for y, g in enumerate(has_gold + [False] * (max_gap + 1)):
        if g:
            if start is None:
                start = y
            gap = 0
        elif start is not None:
            gap += 1
            if gap > max_gap:
                end = y - gap + 1
                if end - start >= min_run and (best is None or end - start > best[1] - best[0]):
                    best = (start, end)
                start, gap = None, 0
    if not best:
        return None
    top = best[0]
    arc = [x for y in range(top, min(H, top + max(2, int(4 * scale)))) for x in gold_xs[y]]
    if not arc:
        return None
    cx = (min(arc) + max(arc)) / 2
    return int(cx), top + int(48 * scale)


def read_hp_from_strip(strip, scale):
    """OCR the HP number in the box just left of the gold ring. scale = height / 1440."""
    ring = find_ring(strip, scale)
    if ring is None:
        return None
    cx, cy = ring
    ring_left = cx - 48 * scale
    box = (int(ring_left - 80 * scale), int(cy - 17 * scale), int(ring_left - 3 * scale), int(cy + 25 * scale))
    if box[0] < 0 or box[1] < 0:
        return None
    crop = strip.crop(box)
    g = ImageOps.grayscale(crop)
    factor = max(3, int(round(4 / max(scale, 0.25))))  # upscale small text more
    g = g.resize((g.width * factor, g.height * factor), Image.LANCZOS)
    g = g.point(lambda p: 255 if p > 150 else 0).filter(ImageFilter.MedianFilter(5))  # drop specks
    g = ImageOps.expand(ImageOps.invert(g), border=20, fill=255)  # margin helps tesseract
    digits = ""
    for psm in (7, 8, 10):  # line, word, then single character
        text = pytesseract.image_to_string(g, config=f"--psm {psm} -c tessedit_char_whitelist=0123456789")
        digits = "".join(ch for ch in text if ch.isdigit())
        if digits:
            break
    if not digits:
        return None
    hp = int(digits)
    return hp if 0 <= hp <= 150 else None


class HealthReader(StageReader):
    """Same window handling and smoothing as StageReader, but reads your HP."""

    def __init__(self, **kwargs):
        kwargs.setdefault("region", HP_STRIP)
        super().__init__(**kwargs)
        self.anchor = "right"
        self.hp = None

    def read_once(self):
        with mss.mss() as sct:
            area = self._area(sct)
            if area is None:
                return None
            shot = sct.grab(region_box(area, self.region, self.anchor))
            strip = Image.frombytes("RGB", shot.size, shot.rgb)
        return read_hp_from_strip(strip, area["height"] / REF_H)

    def update(self):
        if not AVAILABLE:
            return self.hp
        try:
            reading = self.read_once()
        except Exception:
            return self.hp
        if reading is None:
            return self.hp
        if reading == self._candidate:
            self._count += 1
        else:
            self._candidate, self._count = reading, 1
        if self._count >= self.confirm_reads:
            self.hp = reading
        return self.hp

    def reset(self):
        super().reset()
        self.hp = None
