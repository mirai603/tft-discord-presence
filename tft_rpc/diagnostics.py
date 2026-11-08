"""Saves the screenshots the HP/placement reader used, marked up with what it
found, so misreads can be checked afterwards.

Files go to diagnostics/hp/ in the project folder (git-ignored), named
  20261108-161406_hp-7_place-1.png
Each image is the player-list strip with:
  green box  = where your HP was read
  cyan ticks = other players' rows that were counted
  grey ticks = rows found but ignored (off the list's spacing)
  top label  = the values read
Only the newest `max_files` images are kept.
"""

import os
import time

from PIL import ImageDraw


class HPDiagnostics:
    def __init__(self, folder, max_files=1000, mode="all"):
        self.folder = folder
        self.max_files = max(10, int(max_files))
        self.mode = mode  # "all" = every read, "changes" = only when the reading changes
        self._last = None
        self._count = 0
        os.makedirs(folder, exist_ok=True)

    def save(self, strip, scale, hp_box, my_cy, counted, ignored, hp, place):
        key = (hp, place)
        if self.mode == "changes" and key == self._last:
            return
        self._last = key
        try:
            img = strip.convert("RGB")
            d = ImageDraw.Draw(img)
            W = img.size[0]
            if hp_box:
                d.rectangle(hp_box, outline=(0, 255, 0), width=2)
            for y in counted:
                d.line((W - int(124 * scale), y, W - int(93 * scale), y), fill=(0, 255, 255), width=2)
            for y in ignored:
                d.line((W - int(124 * scale), y, W - int(93 * scale), y), fill=(150, 150, 150), width=1)
            label = f"HP {hp if hp is not None else '?'}  place {place if place is not None else '?'}"
            d.rectangle((0, 0, W, 14), fill=(0, 0, 0))
            d.text((3, 1), label, fill=(255, 255, 255))
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
