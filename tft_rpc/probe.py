"""Probe what the new standalone TFT client / Riot Client expose locally.

    python -m tft_rpc probe

Prints a summary and writes probe_output.json next to config.json. Only YOUR
TFT presence is kept (friends' presences and login tokens are left out).
"""

import base64
import json
import os
import ssl
import urllib.error
import urllib.request

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
PROGRAMDATA = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
RIOT_LOCKFILE = os.path.join(LOCALAPPDATA, "Riot Games", "Riot Client", "Config", "lockfile")
TFT_LOCKFILE = os.path.join(PROGRAMDATA, "Riot Games", "Metadata", "teamfighttactics.live",
                            "teamfighttactics.live.lockfile")
TFT_DATA_DIR = os.path.join(LOCALAPPDATA, "Riot Games", "Teamfight Tactics")

LOG_DIRS = [
    os.path.join(LOCALAPPDATA, "TFTClient", "Saved", "Logs"),
    os.path.join(LOCALAPPDATA, "Riot Games", "Teamfight Tactics", "Saved", "Logs"),
    r"C:\Riot Games\Teamfight Tactics\Live\TFTClient\Saved\Logs",
    r"C:\Riot Games\Teamfight Tactics\Live\Saved\Logs",
    r"C:\Riot Games\Teamfight Tactics\Live\Logs",
]
LOG_KEYWORDS = ("stage", "round", "health", "hp", "combat", "carousel", "augment")


def _redacted_shape(path):
    """Describe the lockfile's format without revealing any secret in it."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError as e:
        return repr(e)
    try:
        obj = json.loads(raw)
        return {"format": "json", "keys": list(obj) if isinstance(obj, dict) else type(obj).__name__}
    except ValueError:
        pass
    parts = raw.split(":")
    return {"format": "text", "length": len(raw), "colon_parts": len(parts),
            "parts": [p if p.isdigit() or len(p) < 16 and p.isalpha() else f"<{len(p)} chars>" for p in parts]}


def _log_hits(logs, max_hits=40):
    """Lines from the newest log that mention round/stage/health words."""
    newest = None
    for d in logs:
        for root, _dirs, files in os.walk(d):
            for f in files:
                if f.lower().endswith((".log", ".txt")):
                    full = os.path.join(root, f)
                    if not newest or os.path.getmtime(full) > os.path.getmtime(newest):
                        newest = full
    if not newest:
        return "no log files"
    hits = []
    with open(newest, encoding="utf-8", errors="replace") as fh:
        for line in fh.readlines()[-5000:]:
            low = line.lower()
            if any(k in low for k in LOG_KEYWORDS) and "token" not in low:
                hits.append(line.strip()[:250])
    return {"file": newest, "matching_lines": hits[-max_hits:]}


ME_ENDPOINTS =["/chat/v1/session", "/player-account/aliases/v1/active", "/riot-client-auth/v1/userinfo"]
TFT_API_GUESSES = ["/help", "/swagger/v3/openapi.json", "/openapi.json", "/"]


def _get(url, auth=None, timeout=2):
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Basic {auth}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    context=_CTX, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(body)
            except ValueError:
                return r.status, body[:2000]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return None, repr(e)


def _lockfile(path):
    try:
        with open(path, encoding="utf-8") as f:
            parts = f.read().strip().split(":")
        port, password = parts[2], parts[3]
        return port, base64.b64encode(f"riot:{password}".encode()).decode()
    except (OSError, ValueError, IndexError) as e:
        return None, repr(e)


def _b64json(v):
    if not isinstance(v, str) or not v:
        return v or None
    for f in (json.loads, lambda s: json.loads(base64.b64decode(s + "=" * (-len(s) % 4)))):
        try:
            return f(v)
        except Exception:
            pass
    return "(undecodable)"


def _find_puuid(obj):
    if isinstance(obj, dict):
        for k in ("puuid", "sub"):
            if isinstance(obj.get(k), str) and len(obj[k]) > 30:
                return obj[k]
        for v in obj.values():
            p = _find_puuid(v)
            if p:
                return p
    return None


def _listing(path, max_entries=60):
    out = []
    for root, dirs, files in os.walk(path):
        depth = root[len(path):].count(os.sep)
        if depth > 3:
            dirs[:] = []
            continue
        for f in files:
            full = os.path.join(root, f)
            try:
                out.append(f"{os.path.relpath(full, path)}  ({os.path.getsize(full)} bytes)")
            except OSError:
                pass
            if len(out) >= max_entries:
                return out
    return out or "empty or missing"


def run(out_dir):
    report = {}

    try:
        import psutil
        report["processes"] = sorted({
            p.info["name"] for p in psutil.process_iter(["name"])
            if any(k in (p.info["name"] or "").lower() for k in ("riot", "tft", "league"))
        })
    except ImportError:
        report["processes"] = "psutil not installed"

    # --- Riot Client API ---
    port, auth = _lockfile(RIOT_LOCKFILE)
    rc = {}
    if port:
        base = f"https://127.0.0.1:{port}"
        puuid = None
        for ep in ME_ENDPOINTS:
            status, body = _get(base + ep, auth)
            rc[ep] = {"status": status}
            puuid = puuid or (_find_puuid(body) if status == 200 else None)
        rc["my_puuid_found"] = bool(puuid)

        status, sessions = _get(base + "/product-session/v1/external-sessions", auth)
        if isinstance(sessions, dict):
            rc["sessions"] = {k: {"productId": v.get("productId"), "phase": v.get("phase")}
                              for k, v in sessions.items()}

        status, pres = _get(base + "/chat/v4/presences", auth)
        tft = []
        for p in (pres or {}).get("presences", []) if isinstance(pres, dict) else []:
            if p.get("product") != "teamfighttactics":
                continue
            if puuid and p.get("puuid") != puuid:
                continue
            tft.append({
                "is_me": p.get("puuid") == puuid if puuid else "unknown",
                "state": p.get("state"),
                "packedData": _b64json(p.get("packedData")),
                "private": _b64json(p.get("private")),
            })
        rc["tft_presences"] = tft
    else:
        rc["error"] = auth
    report["riot_client"] = rc

    # --- TFT client's own lockfile / API ---
    tport, tauth = _lockfile(TFT_LOCKFILE)
    if tport:
        tapi = {"port": tport}
        for ep in TFT_API_GUESSES:
            status, body = _get(f"https://127.0.0.1:{tport}{ep}", tauth)
            if status is None:  # maybe plain http
                status, body = _get(f"http://127.0.0.1:{tport}{ep}", tauth)
            tapi[ep] = {"status": status, "body": body if not isinstance(body, str) else body[:1500]}
        report["tft_client_api"] = tapi
    else:
        report["tft_client_api"] = {"error": tauth, "lockfile_shape": _redacted_shape(TFT_LOCKFILE)}

    # --- Unreal log folders (may record round/stage changes) ---
    import glob
    extra = glob.glob(os.path.join(LOCALAPPDATA, "*", "Saved", "Logs")) + \
        glob.glob(r"C:\Riot Games\Teamfight Tactics\**\Saved\Logs", recursive=True)
    logs = {}
    for d in LOG_DIRS + [e for e in extra if "tft" in e.lower() or "teamfight" in e.lower()]:
        if os.path.isdir(d):
            logs[d] = _listing(d, 20)
    report["log_folders"] = logs or "none found"
    report["log_tail_hits"] = _log_hits(logs)

    # --- Old in-game API ---
    status, body = _get("https://127.0.0.1:2999/liveclientdata/allgamedata")
    report["port_2999"] = {"status": status, "body": body if status else "not listening"}

    # --- TFT data folder (Unreal logs may carry round/stage info) ---
    report["tft_data_folder"] = _listing(TFT_DATA_DIR)

    path = os.path.join(out_dir, "probe_output.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(json.dumps(report, indent=2, default=str)[:6000])
    print(f"\nSaved to {path}")
