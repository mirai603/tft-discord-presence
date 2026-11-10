"""Reads your own account details from the Riot Client's local API (via its
lockfile): your account id (to pick you out of the end-of-game stats) and your
Riot ID and region (only needed for the optional rank/LP lookup)."""

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
        puuid = self.my_puuid()
        for p in (self._get("/chat/v4/presences") or {}).get("presences", []):
            if p.get("puuid") == puuid:
                region = _b64json(p.get("packedData")).get("region")
                if region:
                    return region.upper()
        return None

    def my_puuid(self):
        if not self._puuid:
            s = self._get("/chat/v1/session") or {}
            self._puuid = s.get("puuid")
        return self._puuid
