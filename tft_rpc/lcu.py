"""Talks to the League Client (LCU) API, which the Riot/League client exposes
locally. Gives us the queue (Ranked / Normal / Hyper Roll / Double Up), the
lobby/queue/in-game phase, and your TFT rank."""

import base64
import json
import os
import ssl
import urllib.request
from urllib.error import URLError

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

DEFAULT_LOCKFILES = [
    r"C:\Riot Games\League of Legends\lockfile",
    r"D:\Riot Games\League of Legends\lockfile",
    "/Applications/League of Legends.app/Contents/LoL/lockfile",
]

# Fallback names if the session doesn't include a readable description.
TFT_QUEUES = {
    1090: "Normal",
    1100: "Ranked",
    1130: "Hyper Roll",
    1160: "Double Up",
    1110: "Tutorial",
}


def _find_lockfile(custom=None):
    for path in ([custom] if custom else []) + DEFAULT_LOCKFILES:
        if path and os.path.isfile(path):
            return path
    # Last resort: ask psutil where LeagueClientUx.exe lives (optional dependency).
    try:
        import psutil
        for p in psutil.process_iter(["name", "exe"]):
            if (p.info["name"] or "").lower().startswith("leagueclientux"):
                candidate = os.path.join(os.path.dirname(p.info["exe"]), "lockfile")
                if os.path.isfile(candidate):
                    return candidate
    except Exception:
        pass
    return None


class LCU:
    def __init__(self, lockfile_path=None):
        self.lockfile_path = lockfile_path
        self._auth = None
        self._port = None
        self.source = None

    def _load(self):
        port, password = self._from_lockfile() or self._from_process() or (None, None)
        if not port:
            return False
        self._port = port
        self._auth = base64.b64encode(f"riot:{password}".encode()).decode()
        return True

    def _from_lockfile(self):
        path = _find_lockfile(self.lockfile_path)
        self.source = None
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as f:
                _name, _pid, port, password, _proto = f.read().strip().split(":")
        except (OSError, ValueError):
            return None
        self.source = f"lockfile: {path}"
        return port, password

    def _from_process(self):
        """Read port + token from LeagueClientUx's command line. Works no matter
        where League is installed."""
        try:
            import psutil
        except ImportError:
            return None
        for p in psutil.process_iter(["name", "cmdline"]):
            if not (p.info["name"] or "").lower().startswith("leagueclientux"):
                continue
            args = p.info["cmdline"] or []
            port = next((a.split("=", 1)[1] for a in args if a.startswith("--app-port=")), None)
            token = next((a.split("=", 1)[1] for a in args if a.startswith("--remoting-auth-token=")), None)
            if port and token:
                self.source = "LeagueClientUx process command line"
                return port, token
        return None

    def diagnose(self):
        """Explain why the client can't be reached (used by `dump`)."""
        lines = []
        try:
            import psutil
            names = {(p.info["name"] or "") for p in psutil.process_iter(["name"])}
            ux = [n for n in names if n.lower().startswith("leagueclient")]
            lines.append(f"League client processes running: {ux or 'NONE'}")
        except ImportError:
            lines.append("psutil not installed (pip install psutil) - can't inspect processes")
        lines.append(f"Lockfile found at: {_find_lockfile(self.lockfile_path) or 'not found'}")
        ok = self._load()
        lines.append(f"Credentials: {'from ' + self.source if ok else 'NOT FOUND'}")
        if ok:
            try:
                req = urllib.request.Request(
                    f"https://127.0.0.1:{self._port}/lol-gameflow/v1/gameflow-phase",
                    headers={"Authorization": f"Basic {self._auth}"},
                )
                with urllib.request.urlopen(req, context=_CTX, timeout=2) as r:
                    lines.append(f"Gameflow phase: {r.read().decode()}")
            except Exception as e:
                lines.append(f"Request to client failed: {e!r}")
        return "\n".join(lines)

    def get(self, endpoint):
        # The lockfile changes every time the client restarts, so reload on failure.
        for attempt in range(2):
            if not self._auth and not self._load():
                return None
            req = urllib.request.Request(
                f"https://127.0.0.1:{self._port}{endpoint}",
                headers={"Authorization": f"Basic {self._auth}", "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, context=_CTX, timeout=1.5) as r:
                    return json.loads(r.read().decode("utf-8"))
            except URLError as e:
                if getattr(e, "code", None) == 404:
                    return None
                self._auth = None
            except (OSError, ValueError):
                self._auth = None
        return None

    def session(self):
        """Return {'phase', 'queue_id', 'queue_name', 'is_tft'} or None."""
        s = self.get("/lol-gameflow/v1/session")
        if not s:
            phase = self.get("/lol-gameflow/v1/gameflow-phase")
            return {"phase": phase, "queue_id": None, "queue_name": None, "is_tft": None} if phase else None
        queue = (s.get("gameData") or {}).get("queue") or {}
        qid = queue.get("id")
        is_tft = str(queue.get("gameMode", "")).upper() == "TFT" or qid in TFT_QUEUES
        name = TFT_QUEUES.get(qid) or queue.get("description") or queue.get("shortName")
        return {"phase": s.get("phase"), "queue_id": qid, "queue_name": name, "is_tft": is_tft}

    def tft_rank(self):
        """Return e.g. 'Gold II · 45 LP', or None if unranked/unavailable."""
        stats = self.get("/lol-ranked/v1/current-ranked-stats") or {}
        entry = (stats.get("queueMap") or {}).get("RANKED_TFT") or {}
        tier = entry.get("tier")
        if not tier or tier in ("NONE", ""):
            return None
        div = entry.get("division")
        tier = tier.title()
        if div and div not in ("NA", "") and tier not in ("Master", "Grandmaster", "Challenger"):
            tier = f"{tier} {div}"
        return f"{tier} · {entry.get('leaguePoints', 0)} LP"
