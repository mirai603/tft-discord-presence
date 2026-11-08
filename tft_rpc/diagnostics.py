"""Saves the screenshots the HP/placement reader used, marked up with what it
found, so misreads can be checked afterwards.

Files go to diagnostics/hp/ in the project folder (git-ignored), named
  20261108-161406_hp-7_place-1.png
Each image has the raw player-list strip on the left (untouched, so it can be
re-tested) and a marked-up copy on the right:
  green box   = where your HP was read
  slot ticks  = the 8 list slots with their gold-arrow score; green = chosen
  top label   = the values read
Only the newest `max_files` images are kept. Saving is off until `enabled` is
set (the tray's "Save HP screenshots" item, or diagnostics.hp_screenshots in
config.json).
"""

import os
import time

from PIL import Image, ImageDraw


class HPDiagnostics:
    def __init__(self, folder, max_files=1000, mode="all", enabled=False):
        self.folder = folder
        self.max_files = max(10, int(max_files))
        self.mode = mode  # "all" = every read, "changes" = only when the reading changes
        self._last = None
        self._count = 0
        self.enabled = enabled

    def save(self, strip, scale, hp_box, slot_scores, hp, place):
        if not self.enabled:
            return
        key = (hp, place)
        if self.mode == "changes" and key == self._last:
            return
        self._last = key
        try:
            from .screen_reader import SLOT_PITCH, SLOT_Y0
            raw = strip.convert("RGB")
            marked = raw.copy()
            d = ImageDraw.Draw(marked)
            W, H = marked.size
            if hp_box:
                d.rectangle(hp_box, outline=(0, 255, 0), width=2)
            for m, score in enumerate(slot_scores or []):
                y = int((SLOT_Y0 + SLOT_PITCH * m) * scale)
                chosen = place is not None and m == place - 1
                colour = (0, 255, 0) if chosen else (160, 160, 160)
                d.line((W - 12, y, W, y), fill=colour, width=2)
                d.text((W - 30, y - 12), str(score), fill=colour)
            label = f"HP {hp if hp is not None else '?'}  place {place if place is not None else '?'}"
            d.rectangle((0, 0, W, 14), fill=(0, 0, 0))
            d.text((3, 1), label, fill=(255, 255, 255))
            img = Image.new("RGB", (W * 2 + 6, H), (255, 0, 255))
            img.paste(raw, (0, 0))
            img.paste(marked, (W + 6, 0))
            os.makedirs(self.folder, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            name = f"{stamp}_hp-{hp if hp is not None else 'none'}_place-{place if place is not None else 'none'}.png"
            path = os.path.join(self.folder, name)
            if os.path.exists(path):  # two reads in the same second
                path = path[:-4] + f"_{int(time.time() * 1000) % 1000:03d}.png"
            img.save(path, optimize=True)
            self._count += 1
            if self._count % 20 == 0:
                self._prune()
        except Exception:
            pass  # diagnostics must never break the presence

    def _prune(self):
        files = sorted(f for f in os.listdir(self.folder) if f.endswith(".png"))
        for f in files[:-self.max_files]:
            try:
                os.remove(os.path.join(self.folder, f))
            except OSError:
                pass
