"""Reads the in-game Live Client Data API that the TFT game process serves on
https://127.0.0.1:2999 while a match is running. It uses a self-signed
certificate, so verification is skipped for this localhost-only call."""

import json
import ssl
import urllib.request
from urllib.error import URLError

BASE = "https://127.0.0.1:2999/liveclientdata"
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def get_all_game_data(timeout=1.5):
    """Return the raw allgamedata JSON, or None if no game is running."""
    try:
        with urllib.request.urlopen(f"{BASE}/allgamedata", context=_CTX, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (URLError, OSError, ValueError):
        return None


def _first(d, *paths):
    """Return the first non-None value among dotted key paths."""
    for path in paths:
        cur = d
        for key in path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                cur = None
                break
        if cur is not None:
            return cur
    return None


def parse(data):
    """Pull the fields we care about out of allgamedata.

    Riot exposes far less for TFT than for League, and field names have shifted
    between patches, so every lookup is defensive. Run `python -m tft_rpc dump`
    during a game to see exactly what your client returns.
    """
    if not data:
        return None
    game = data.get("gameData", {})
    mode = str(game.get("gameMode", "")).upper()
    if mode and mode != "TFT":
        return None  # a League game, not TFT

    ap = data.get("activePlayer", {}) or {}
    health = _first(ap, "championStats.currentHealth", "health", "currentHealth")
    gold = _first(ap, "currentGold", "gold")
    level = _first(ap, "level")
    players = data.get("allPlayers", []) or []
    alive = [p for p in players if not p.get("isDead")]

    def as_int(v):
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return None

    return {
        "game_time": float(game.get("gameTime", 0) or 0),
        "health": as_int(health),
        "gold": as_int(gold),
        "level": as_int(level),
        "players_total": len(players) or None,
        "players_alive": len(alive) if players else None,
        "name": _first(ap, "riotIdGameName", "summonerName", "riotId"),
    }
