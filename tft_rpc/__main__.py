"""Entry point.

    python -m tft_rpc            run the rich presence
    python -m tft_rpc dump       print what each data source reports right now
    python -m tft_rpc calibrate  save screenshots to tune the stage OCR region
    python -m tft_rpc read-hp <image|folder> [--mode auto|normal|double-up] [--save]
                                 run the HP/placement reader on saved screenshots
    python -m tft_rpc tray       run in the background with a system-tray icon
    python -m tft_rpc install-startup / uninstall-startup
                                 start (or stop starting) the tray app when you log in
"""

import json
import sys

from .factory import load_config, make_hp_reader, make_ranked, make_reader, tft_process_running
from .log_watcher import LogWatcher
from .riot_client import RiotClient

def cmd_dump(cfg):
    rc = RiotClient()
    print("== TFT log session ==")
    running = tft_process_running()
    print("TFT running:", running)
    print(json.dumps(LogWatcher().session(rc.my_puuid()) if running else None, indent=2))
    print("== TFT rank (Riot API) ==")
    ranked = make_ranked(cfg, rc)
    if ranked:
        print(f"Riot ID: {ranked.riot_id}  platform: {ranked.platform}")
        ranked.refresh()
        print(ranked.text(1100), "| Double Up:", ranked.text(1160))
    else:
        print("No riot_api_key in config.json")
    reader = make_reader(cfg)
    if reader:
        print("== Stage OCR (single read) ==")
        print(reader.read_once())
    hp_reader = make_hp_reader(cfg)
    if hp_reader:
        hp_reader.only_when_focused = False
        print("== HP / placement (single read) ==")
        print(hp_reader.read_board())


def cmd_calibrate(cfg):
    reader = make_reader(cfg)
    if not reader:
        sys.exit("OCR libraries not installed.")
    full, crop = reader.save_debug_images()
    print(f"Captured from: {reader.last_source}")
    hp_reader = make_hp_reader(cfg)
    if hp_reader:
        hp_reader.only_when_focused = False
        hp, place = hp_reader.read_board()
        print(f"HP read: {hp!r}   placement: {place!r}")
    print(f"Saved {full} (red box = region) and {crop} (what OCR sees).")
    print(f"OCR read: {reader.read_once()!r}")
    print("Adjust stage_ocr.region in config.json until the box hugs the '3-2' text.")


def cmd_run(cfg):
    """Run in this terminal (Ctrl+C to stop). For the background version use `tray`."""
    from .runner import PresenceRunner
    runner = PresenceRunner(cfg)
    try:
        runner.run()
    except KeyboardInterrupt:
        runner.stop()
        print("Stopped.")


def main():
    cfg = load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "read-hp":
        from . import hp_check
        return hp_check.main(sys.argv[2:])
    if cmd == "tray":
        from . import tray
        return tray.main()
    if cmd in ("install-startup", "uninstall-startup"):
        from . import startup
        ok = startup.install() if cmd == "install-startup" else startup.uninstall()
        return print(ok)
    {"run": cmd_run, "dump": cmd_dump, "calibrate": cmd_calibrate}.get(cmd, cmd_run)(cfg)


if __name__ == "__main__":
    main()
