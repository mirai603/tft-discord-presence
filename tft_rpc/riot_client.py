"""Gets TFT status from the Riot Client's local API (the standalone TFT client,
Nov 2026+). Your own TFT chat presence tells us the queue and whether you're
in a match; this is the same data your friends list shows."""

import base64
import json
import os
import ssl
import urllib.error
import urllib.request

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

LOCKFILE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Riot Games", "Riot Client",
                        "Config", "lockfile")

QUEUES = {
    1090: "Normal",
    1100: "Ranked",
    1130: "Hyper Roll",
    1160: "Double Up",
    1220: "Tocker's Trials",
}


def _b64json(v):
    if not isinstance(v, str) or not v:
        return {}
    try:
        return json.loads(base64.b64decode(v + "=" * (-len(v) % 4)))
    except Exception:
        return {}


class RiotClient:
    def __init__(self):
        self._port = self._auth = self._puuid = None

    def _load(self):
        try:
            with open(LOCKFILE, encoding="utf-8") as f:
                parts = f.read().strip().split(":")
            self._port = parts[2]
            self._auth = base64.b64encode(f"riot:{parts[3]}".encode()).decode()
            self._puuid = None
            return True
        except (OSError, IndexError):
            self._port = self._auth = None
            return False

    def _get(self, endpoint):
        for _ in range(2):  # lockfile changes when the Riot Client restarts
            if not self._auth and not self._load():
                return None
            req = urllib.request.Request(
                f"https://127.0.0.1:{self._port}{endpoint}",
                headers={"Authorization": f"Basic {self._auth}", "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, context=_CTX, timeout=1.5) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError:
                return None
            except (OSError, ValueError):
                self._auth = None
        return None

    def riot_id(self):
        """Your Riot ID as 'Name#TAG', from the Riot Client's chat session."""
        s = self._get("/chat/v1/session") or {}
        name, tag = s.get("game_name"), s.get("game_tag")
        return f"{name}#{tag}" if name and tag else None

    def platform(self):
        """Your platform id, e.g. 'NA1', from your own TFT presence."""
        puuid = self._my_puuid()
        for p in (self._get("/chat/v4/presences") or {}).get("presences", []):
            if p.get("puuid") == puuid:
                region = _b64json(p.get("packedData")).get("region")
                if region:
                    return region.upper()
        return None

    def _my_puuid(self):
        if not self._puuid:
            s = self._get("/chat/v1/session") or {}
            self._puuid = s.get("puuid")
        return self._puuid

    def tft_running(self):
        sessions = self._get("/product-session/v1/external-sessions") or {}
        return any(v.get("productId") == "teamfighttactics" for v in sessions.values()
                   if isinstance(v, dict))

    def session(self):
        """Return {'phase','queue_id','queue_name','is_tft','progress'} or None.

        phase is 'Lobby' when TFT is open but you're not in a match, and
        'InProgress' during a match.
        """
        puuid = self._my_puuid()
        pres = self._get("/chat/v4/presences") or {}
        mine = None
        for p in pres.get("presences", []):
            if p.get("product") == "teamfighttactics" and (not puuid or p.get("puuid") == puuid):
                mine = p
                break
        if not mine:
            return {"phase": "Lobby", "queue_id": None, "queue_name": None, "is_tft": True,
                    "progress": None} if self.tft_running() else None

        activity = _b64json(mine.get("packedData")).get("activity") or {}
        mode = str(activity.get("mode", ""))
        qid = None
        if "queueId_" in mode:
            try:
                qid = int(mode.rsplit("queueId_", 1)[1])
            except ValueError:
                pass
        progress = (activity.get("progress") or {}).get("value")
        in_game = bool(progress) and progress != "NotInGame"
        return {
            "phase": "InProgress" if in_game else "Lobby",
            "queue_id": qid,
            "queue_name": QUEUES.get(qid),
            "is_tft": True,
            "progress": progress if in_game else None,
        }
