"""Builds the readers/clients from config.json. Shared by the terminal and tray."""

import json
import os

from .riot_client import RiotClient
from .ranked import Ranked
from .screen_reader import AVAILABLE as OCR_AVAILABLE, HealthReader, StageReader

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIAG_DIR = os.path.join(HERE, "diagnostics", "hp")


def load_config():
    """Read config.json, creating it from config.example.json on first run."""
    path = os.path.join(HERE, "config.json")
    example = os.path.join(HERE, "config.example.json")
    if not os.path.exists(path) and os.path.exists(example):
        import shutil
        shutil.copyfile(example, path)
        print("Created config.json from config.example.json; set discord_client_id in it.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def make_reader(cfg):
    ocr = cfg.get("stage_ocr", {})
    if not ocr.get("enabled", True):
        return None
    if not OCR_AVAILABLE:
        print("[!] Stage OCR disabled: install mss, pillow and pytesseract to enable it.")
        return None
    return StageReader(
        region=ocr.get("region", [0.33, 0.002, 0.15, 0.03]),
        monitor=ocr.get("monitor", 1),
        tesseract_cmd=ocr.get("tesseract_cmd") or None,
        confirm_reads=ocr.get("confirm_reads", 2),
        capture=ocr.get("capture", "window"),
        only_when_focused=ocr.get("only_when_focused", True),
    )


def tft_process_running(rc):
    try:
        import psutil
        return any((p.info["name"] or "").lower().startswith("tftclient")
                   for p in psutil.process_iter(["name"]))
    except ImportError:
        return rc.tft_running()


def get_session(rc, lcu, logw=None):
    """TFT's own log first, then the Riot Client presence, then the old League client."""
    if logw is not None and tft_process_running(rc):
        s = logw.session(rc._my_puuid())
        if s:
            return s
    return rc.session() or lcu.session()


def make_hp_reader(cfg):
    ocr = cfg.get("stage_ocr", {})
    if not cfg.get("hp_ocr", {}).get("enabled", True) or not OCR_AVAILABLE:
        return None
    diag_cfg = cfg.get("diagnostics", {})
    from .diagnostics import HPDiagnostics
    diagnostics = HPDiagnostics(DIAG_DIR,
                                max_files=diag_cfg.get("max_files", 1000),
                                mode=diag_cfg.get("mode", "all"),
                                enabled=bool(diag_cfg.get("hp_screenshots", False)))
    return HealthReader(
        diagnostics=diagnostics,
        monitor=ocr.get("monitor", 1),
        tesseract_cmd=ocr.get("tesseract_cmd") or None,
        confirm_reads=ocr.get("confirm_reads", 2),
        capture=ocr.get("capture", "window"),
        only_when_focused=ocr.get("only_when_focused", True),
    )


def make_ranked(cfg, rc):
    key = cfg.get("riot_api_key", "")
    if not key or key.startswith("PUT"):
        return None
    platform = cfg.get("riot_platform") or rc.platform() or "NA1"
    riot_id = cfg.get("riot_id") or rc.riot_id()
    return Ranked(key, platform, riot_id)
