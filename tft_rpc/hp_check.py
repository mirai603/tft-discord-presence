"""Run the HP / placement reader on a saved image, for diagnostics.

    python -m tft_rpc read-hp <image or folder> [--mode auto|normal|double-up] [--save]

In auto mode it uses Double Up for diagnostic files named "_du_", otherwise it
tries both readers and prefers Double Up if that finds a full match. Use --mode
to force one; the app itself always knows the mode from the queue.

Accepts:
  - a diagnostic image from diagnostics/hp/ (raw strip on the left, marked copy on the right)
  - a full screenshot of the TFT window (any size)
  - a folder of either (reads every .png/.jpg in it)

From Python:
    from tft_rpc.hp_check import read_hp_image
    result = read_hp_image("diagnostics/hp/20261109-224015_du_hp-1_place-2.png")
    print(result["hp"], result["placement"], result["mode"])
"""

import os
import sys

from . import screen_reader as sr
from .tft_window import region_box


def _strip_from(img):
    """Return (player-list strip, scale, description) for any supported image."""
    W, H = img.size
    # Diagnostic image: two equal halves with a magenta divider in the middle.
    mid = (W - 6) // 2
    if W > 20 and all(img.getpixel((mid + 2, y))[:3] == (255, 0, 255) for y in (0, H // 2, H - 1)):
        strip = img.crop((0, 0, mid, H))
        game_h = H / sr.HP_STRIP[3]  # the strip is 78% of the game's height
        return strip, game_h / sr.REF_H, f"diagnostic image (strip {mid}x{H}, game height ~{game_h:.0f}px)"
    # Anything else is treated as a full screenshot of the TFT window.
    area = {"left": 0, "top": 0, "width": W, "height": H}
    b = region_box(area, sr.HP_STRIP, "right")
    strip = img.crop((b["left"], b["top"], b["left"] + b["width"], b["top"] + b["height"]))
    return strip, H / sr.REF_H, f"full screenshot ({W}x{H})"


def read_hp_image(path_or_image, mode="auto"):
    """Read HP and placement from one image. mode: "auto", "normal" or "double-up".

    Returns a dict: hp, placement, mode (which reader gave the answer), source,
    and the details behind each reader's decision.
    """
    if not sr.OCR_LIBS:
        raise RuntimeError("Needs pillow and pytesseract: python -m pip install pillow pytesseract")
    # Diagnostic files from Double Up games have "_du_" in their name.
    if mode == "auto" and isinstance(path_or_image, str) and "_du_" in os.path.basename(path_or_image):
        mode = "double-up"
    img = path_or_image if hasattr(path_or_image, "crop") else sr.Image.open(path_or_image)
    img = img.convert("RGB")
    strip, scale, source = _strip_from(img)
    out = {"hp": None, "placement": None, "mode": None, "source": source, "scale": round(scale, 3)}

    if mode in ("auto", "normal"):
        slot, cy, scores = sr.find_my_slot(strip, scale)
        hp = sr.read_my_hp(strip, scale, cy) if slot is not None else None
        out["normal"] = {"hp": hp, "placement": None if slot is None else slot + 1,
                         "gold_arrow_score_per_slot": scores, "row_y": cy}
    if mode in ("auto", "double-up"):
        team, cy, matches = sr.find_my_team(strip, scale)
        hp = None
        if team is not None:
            _box, hp = sr.read_team_hp(strip, scale, cy)
        out["double_up"] = {"hp": hp, "placement": None if team is None or hp is None else team + 1,
                            "name_rows_matched_per_layout": matches, "box_y": cy}

    # The Double Up check is stricter (name layout must match AND the HP must read),
    # so if it finds an answer, prefer it; otherwise use the normal-mode answer.
    # (The app itself never guesses: it knows the mode from the queue.)
    for key, label in (("double_up", "double-up"), ("normal", "normal")):
        r = out.get(key)
        if r and r["hp"] is not None and out["hp"] is None:
            out["hp"], out["placement"], out["mode"] = r["hp"], r["placement"], label
    return out


def _fmt(r):
    if r["hp"] is None:
        return "nothing read"
    team = "Team " if r["mode"] == "double-up" else ""
    return f"{r['hp']} HP, {team}place {r['placement']}  [{r['mode']}]"


def _save_marked(path, img_result, strip_img, scale):
    from PIL import ImageDraw
    m = strip_img.copy()
    d = ImageDraw.Draw(m)
    d.rectangle((0, 0, m.size[0], 14), fill=(0, 0, 0))
    d.text((3, 1), _fmt(img_result), fill=(255, 255, 255))
    out = os.path.splitext(path)[0] + "_check.png"
    m.save(out)
    return out


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    mode = "auto"
    if "--mode" in argv:
        mode = argv[argv.index("--mode") + 1]
    save = "--save" in argv
    target = argv[0]

    # Use the Tesseract path from config.json, like the app does.
    try:
        from .factory import load_config
        cmd = load_config().get("stage_ocr", {}).get("tesseract_cmd")
        if cmd and os.path.exists(cmd):
            sr.pytesseract.pytesseract.tesseract_cmd = cmd
    except Exception:
        pass

    if os.path.isdir(target):
        files = sorted(os.path.join(target, f) for f in os.listdir(target)
                       if f.lower().endswith((".png", ".jpg", ".jpeg")) and not f.endswith("_check.png"))
    else:
        files = [target]
    if not files:
        print("No images found.")
        return

    verbose = len(files) == 1
    for f in files:
        try:
            r = read_hp_image(f, mode)
        except Exception as e:
            print(f"{os.path.basename(f)}: error: {e}")
            continue
        if not verbose:
            print(f"{os.path.basename(f)}: {_fmt(r)}")
            continue
        print(f"File:   {f}")
        print(f"Input:  {r['source']} (scale {r['scale']} of 1440p)")
        if "normal" in r:
            n = r["normal"]
            print(f"Normal mode:    HP {n['hp']}, place {n['placement']}   "
                  f"gold-arrow score per slot {n['gold_arrow_score_per_slot']}")
        if "double_up" in r:
            u = r["double_up"]
            print(f"Double Up mode: HP {u['hp']}, team place {u['placement']}   "
                  f"name rows matched per layout {u['name_rows_matched_per_layout']}")
        print(f"Result: {_fmt(r)}" + ("" if mode != "auto" else "   (auto mode; force with --mode normal|double-up)"))
        if save:
            img = sr.Image.open(f).convert("RGB")
            strip, scale, _ = _strip_from(img)
            print(f"Saved:  {_save_marked(f, r, strip, scale)}")


if __name__ == "__main__":
    main(sys.argv[1:])
