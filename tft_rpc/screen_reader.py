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

# Image reading (PIL + pytesseract) and screen capture (mss) are imported
# separately so saved screenshots can be checked even without mss.
try:
    from PIL import Image, ImageFilter, ImageOps
    import pytesseract
    OCR_LIBS = True
except ImportError:
    OCR_LIBS = False
try:
    import mss
except ImportError:
    mss = None
AVAILABLE = OCR_LIBS and mss is not None


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
        factor = max(3, round(90 / max(1, img.height)))  # small windows get zoomed more
        g = g.resize((g.width * factor, g.height * factor), Image.LANCZOS)
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
# Your HP and placement, from the player list on the right. The list is a fixed
# grid of 8 slots sorted by HP; your HP box is the only one with a gold arrow,
# so the slot where the arrow is gives your placement, and the number in that
# box is your HP. (The portrait rings are HP gauges that drain, so they aren't
# used.) Measurements are from live matches, scaled to 1440p.
# ---------------------------------------------------------------------------

HP_STRIP = [0.89, 0.08, 0.11, 0.78]   # 16:9 fractions, pinned to the right edge
REF_H = 1440


def _ocr_digits(crop, scale, threshold=150):
    """OCR a small box of light digits on a dark background. Returns int or None."""
    g = ImageOps.grayscale(crop)
    factor = max(3, int(round(4 / max(scale, 0.25))))  # upscale small text more
    g = g.resize((g.width * factor, g.height * factor), Image.LANCZOS)
    g = g.point(lambda p: 255 if p > threshold else 0).filter(ImageFilter.MedianFilter(5))
    g = ImageOps.expand(ImageOps.invert(g), border=20, fill=255)  # margin helps tesseract
    for psm in (7, 8, 10):  # line, word, then single character
        text = pytesseract.image_to_string(g, config=f"--psm {psm} -c tessedit_char_whitelist=0123456789")
        digits = "".join(ch for ch in text if ch.isdigit())
        if digits:
            value = int(digits)
            return value if 0 <= value <= 150 else None
    return None


def _is_digit_px(px):
    """HP digits: light grey to white, unsaturated (not the gold borders)."""
    r, g, b = px[:3]
    return min(r, g, b) > 140 and max(r, g, b) - min(r, g, b) < 45


def _runs(strip, x0, x1, scale, min_h, max_h, max_gap=2):
    """Vertical runs of rows containing digit-like pixels between x0..x1.
    Returns [(start, end, pixel_count)]."""
    W, H = strip.size
    px = strip.load()
    x0, x1 = max(0, int(x0)), min(W, int(x1))
    counts = [sum(1 for x in range(x0, x1) if _is_digit_px(px[x, y])) for y in range(H)]
    out, start, gap, total = [], None, 0, 0
    gap_limit = max(1, int(max_gap * scale))
    for y, n in enumerate(counts + [0] * (gap_limit + 2)):
        if n:
            if start is None:
                start, total = y, 0
            gap, total = 0, total + n
        elif start is not None:
            gap += 1
            if gap > gap_limit:
                end = y - gap + 1
                if min_h * scale <= end - start <= max_h * scale:
                    out.append((start, end, total))
                start, gap = None, 0
    return out


# Live-match player list, measured from the game's right edge at 1440p:
# your HP box digits sit 129-180 px from the edge and are ~20 px tall.
MY_DIGITS_X = (180, 129)


def _gold_arrow_score(strip, scale, cy):
    """Gold pixels where your HP box's arrow/left border is (~178-200 px from the
    right edge at 1440p). Other players' rows have names or background there."""
    W, H = strip.size
    px = strip.load()
    x0, x1 = max(0, int(W - 200 * scale)), max(0, int(W - 178 * scale))
    y0, y1 = max(0, int(cy - 14 * scale)), min(H, int(cy + 14 * scale))
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b = px[x, y][:3]
            if r > 150 and g > 105 and b < 95 and r - b > 70:
                n += 1
    return n


# The player list is a fixed grid of 8 slots (knocked-out players stay in it).
# Measured from live matches, in strip pixels at 1440p: first slot centre and
# the distance between slots.
SLOT_Y0 = 149.3
SLOT_PITCH = 106.6
SLOTS = 8


def find_my_slot(strip, scale):
    """Return (slot_index 0-7, your_row_centre_y, [score per slot]) or (None, None, scores).

    Your HP box is the only one with a gold arrow on its left; the slot where it
    shows up is your placement. Nothing here depends on the portrait rings (HP
    gauges that drain) or on other text near the list.
    """
    H = strip.size[1]
    slots = [int((SLOT_Y0 + SLOT_PITCH * m) * scale) for m in range(SLOTS)]
    scores = [_gold_arrow_score(strip, scale, y) if y < H else 0 for y in slots]
    ranked = sorted(range(SLOTS), key=lambda m: scores[m], reverse=True)
    best, second = ranked[0], scores[ranked[1]]
    # The arrow is the only gold there, so the right slot clearly beats the rest.
    if scores[best] < 3 or scores[best] < 2 * second + 2:
        return None, None, scores
    # Centre on your HP digits within the slot (rows can sit a few px off the grid).
    W = strip.size[0]
    y = slots[best]
    near = [(a, b, n) for a, b, n in
            _runs(strip, W - MY_DIGITS_X[0] * scale, W - MY_DIGITS_X[1] * scale, scale, 8, 30)
            if abs((a + b) // 2 - y) <= 25 * scale]
    if near:
        a, b, _ = max(near, key=lambda r: r[2])
        y = (a + b) // 2
    return best, y, scores


# --- Double Up -------------------------------------------------------------
# 4 teams of 2. Each team shares one HP box between its two portraits. The
# list is a fixed grid, except that your team's slot is taller: your two names
# sit a bit further apart and every team below yours is pushed down. So there
# are only 4 possible layouts (one per team being yours). Player names are
# always shown (greyed out once a player is knocked out), so we find the name
# rows and pick the layout they match; that gives your team, i.e. your team's
# placement, and where your team's HP box is.
# Portrait rings, team brackets and HP digits aren't used to find your team:
# rings and brackets are HP gauges that drain, and knocked-out players' grey
# portraits look like digits. Measured from live matches, scaled to 1440p.
DU_TEAMS = 4
DU_TEAM_PITCH = 198.2           # distance between teams
DU_NAME_ROWS = (177.0, 276.1)   # a team's two name rows (first team), strip px
DU_MY_NAME_SHIFT = (9.2, 26.4)  # your team's names sit this much lower (top, bottom)
DU_BELOW_SHIFT = 34.3           # teams below yours are pushed down this far
DU_BOX_Y0 = 227.2               # first team's HP box centre
DU_MY_BOX_SHIFT = 17.2          # your box sits this much below its grid spot
DU_NAMES_X = (281, 143)         # name column, from the right edge


def _is_name_px(c):
    r, g, b = c[:3]
    white = min(r, g, b) > 150 and max(r, g, b) - min(r, g, b) < 60
    gold = r > 170 and g > 130 and b < 120 and r - b > 70   # your own name is gold
    return white or gold


def find_name_rows(strip, scale):
    W, H = strip.size
    px = strip.load()
    x0, x1 = max(0, int(W - DU_NAMES_X[0] * scale)), max(1, int(W - DU_NAMES_X[1] * scale))
    prof = [sum(1 for x in range(x0, x1) if _is_name_px(px[x, y])) for y in range(H)]
    rows, start = [], None
    for y, c in enumerate(prof + [0] * 4):
        if c >= 2 and start is None:
            start = y
        elif c < 2 and start is not None:
            h = y - start
            if 8 * scale <= h <= 29 * scale and sum(prof[start:y]) >= 44 * scale * scale:
                rows.append((start + y) / 2)
            start = None
    return rows


def predicted_name_rows(my_team, scale):
    out = []
    for k in range(DU_TEAMS):
        top, bottom = (r + DU_TEAM_PITCH * k for r in DU_NAME_ROWS)
        if k == my_team:
            top, bottom = top + DU_MY_NAME_SHIFT[0], bottom + DU_MY_NAME_SHIFT[1]
        elif k > my_team:
            top, bottom = top + DU_BELOW_SHIFT, bottom + DU_BELOW_SHIFT
        out += [top * scale, bottom * scale]
    return out


def find_my_team(strip, scale):
    """Return (team_index 0-3, your_box_centre_y, [name rows matched per layout])
    or (None, None, matches)."""
    rows = find_name_rows(strip, scale)
    tol = 5.3 * scale + 0.6
    matches = [sum(1 for p in predicted_name_rows(m, scale) if any(abs(p - r) <= tol for r in rows))
               for m in range(DU_TEAMS)]
    ranked = sorted(range(DU_TEAMS), key=lambda m: matches[m], reverse=True)
    best = ranked[0]
    if matches[best] < 3 or matches[best] <= matches[ranked[1]]:
        return None, None, matches
    box_y = int((DU_BOX_Y0 + DU_TEAM_PITCH * best + DU_MY_BOX_SHIFT) * scale)
    return best, box_y, matches


def read_team_hp(strip, scale, cy):
    """OCR your team's HP. The box shifts sideways with the number's width, so the
    crop is cut just before the team emblem's coloured ring wherever it starts."""
    W = strip.size[0]
    # Centre on the digits first: the predicted position can be a few px off.
    px0 = strip.load()
    x0, x1 = int(W - 140 * scale), int(W - 75 * scale)
    def digits_at(y):
        a = int(9 * scale)
        return sum(1 for yy in range(max(0, y - a), min(strip.size[1], y + a + 1))
                   for x in range(x0, x1) if _is_digit_px(px0[x, yy]))
    cy = max(range(int(cy - 6 * scale), int(cy + 6 * scale) + 1), key=digits_at)
    box = [int(W - 145 * scale), int(cy - 13 * scale), int(W - 66 * scale), int(cy + 13 * scale)]
    crop = strip.crop(tuple(box))
    px = crop.load()
    cw, ch = crop.size
    for x in range(int(cw * 0.35), cw):
        coloured = 0
        for y in range(ch):
            r, g, b = px[x, y][:3]
            if max(r, g, b) > 110 and max(r, g, b) - min(r, g, b) > 80:
                coloured += 1
        if coloured >= max(3, ch // 6):
            box[2] = box[0] + max(1, x - int(2 * scale))
            break
    box = tuple(box)
    return box, _ocr_digits(strip.crop(box), scale)


def my_hp_box(strip, scale, my_cy):
    W = strip.size[0]
    # Left edge stays just right of the gold arrow (178-200 px) so it can't read as a "1".
    return (int(W - 177 * scale), int(my_cy - 17 * scale), int(W - 124 * scale), int(my_cy + 17 * scale))


def read_my_hp(strip, scale, my_cy):
    box = my_hp_box(strip, scale, my_cy)
    if box[0] < 0 or box[1] < 0:
        return None
    return _ocr_digits(strip.crop(box), scale)


class _Smoothed:
    """Only accept a new value after seeing it N times in a row."""

    def __init__(self, confirm):
        self.confirm, self.value, self._cand, self._n = confirm, None, None, 0

    def feed(self, v):
        if v is None:
            return self.value
        if v == self._cand:
            self._n += 1
        else:
            self._cand, self._n = v, 1
        if self._n >= self.confirm:
            self.value = v
        return self.value

    def reset(self):
        self.value, self._cand, self._n = None, None, 0


class HealthReader(StageReader):
    """Reads your HP and placement from the player list (same window handling)."""

    def __init__(self, track_placement=True, diagnostics=None, **kwargs):
        kwargs.setdefault("region", HP_STRIP)
        super().__init__(**kwargs)
        self.anchor = "right"
        self.track_placement = track_placement
        self._hp = _Smoothed(self.confirm_reads)
        self._place = _Smoothed(self.confirm_reads)
        self.diagnostics = diagnostics  # HPDiagnostics or None
        self.double_up = False  # set by the runner from the queue (1160)

    @property
    def hp(self):
        return self._hp.value

    @property
    def placement(self):
        return self._place.value

    def _grab_strip(self):
        with mss.mss() as sct:
            area = self._area(sct)
            if area is None:
                return None, None
            shot = sct.grab(region_box(area, self.region, self.anchor))
            return Image.frombytes("RGB", shot.size, shot.rgb), area["height"] / REF_H

    def read_board(self):
        """Return (my_hp, placement) from one screenshot. In Double Up these are
        your team's HP and your team's placement (1-4)."""
        strip, scale = self._grab_strip()
        if strip is None:
            return None, None
        hp = place = box = None
        if self.double_up:
            team, cy, scores = find_my_team(strip, scale)
            if team is not None:
                box, hp = read_team_hp(strip, scale, cy)
                # Only trust the layout if your team's HP box is really there.
                if hp is not None:
                    place = team + 1 if self.track_placement else None
        else:
            slot, my_cy, scores = find_my_slot(strip, scale)
            if slot is not None:
                box = my_hp_box(strip, scale, my_cy)
                hp = read_my_hp(strip, scale, my_cy)
                place = slot + 1 if self.track_placement else None
        if self.diagnostics:
            self.diagnostics.save(strip, scale, box, scores, hp, place, self.double_up)
        return hp, place

    def update(self):
        if not AVAILABLE:
            return self.hp
        try:
            hp, place = self.read_board()
        except Exception:
            return self.hp
        self._hp.feed(hp)
        self._place.feed(place)
        return self.hp

    def reset(self):
        super().reset()
        self._hp.reset()
        self._place.reset()
