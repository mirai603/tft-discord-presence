"""Map out what the Riot Client's local API offers.

    python -m tft_rpc explore

Read-only: only GET requests, nothing that logs in, changes settings, sends
messages or touches tokens. Saves explore_output.json with each endpoint's
response *shape* (field names and types) rather than your data. Short values
that look TFT-related are kept so we can see what's useful; long strings,
ids and tokens are always replaced with a placeholder.
"""

import base64
import json
import os
import re
import ssl
import urllib.error
import urllib.request

from .factory import HERE
from .riot_client import LOCKFILE

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# Never call anything whose name suggests credentials, purchases or account changes.
SKIP = re.compile(r"token|auth|entitlement|rso|cookie|jwt|password|credential|login|logout|"
                  r"secret|payment|purchase|store|wallet|vanguard|report|delete|kick|ban",
                  re.I)
TFT_HINT = re.compile(r"tft|teamfight|queue|round|stage|health|hp|rank|lp|league|placement|"
                      r"party|lobby|game|match|level|gold|augment|tactician|product|phase|mode|state|region|type|status", re.I)
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# Known Riot Client paths to try if the API won't list itself.
FALLBACK_PATHS = [
    "/product-session/v1/external-sessions", "/product-session/v1/sessions",
    "/product-launcher/v1/products", "/riotclient/region-locale", "/riotclient/affinity",
    "/chat/v1/session", "/chat/v4/presences", "/chat/v4/friends", "/chat/v3/friends",
    "/chat/v5/participants", "/chat/v6/conversations", "/player-account/aliases/v1/active",
    "/riot-client-lifecycle/v1/state", "/patch/v1/installs", "/player-preferences/v1/data",
    "/riot-messaging-service/v1/state", "/social/v1/friends", "/parties/v1/players/me",
    "/restrictions/v1/restrictions", "/product-integration/v1/products",
]


def _auth():
    with open(LOCKFILE, encoding="utf-8") as f:
        parts = f.read().strip().split(":")
    return parts[2], base64.b64encode(f"riot:{parts[3]}".encode()).decode()


def _get(port, auth, path, timeout=3):
    req = urllib.request.Request(f"https://127.0.0.1:{port}{path}",
                                 headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, context=_CTX, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(body)
            except ValueError:
                return r.status, body[:200]
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return None, repr(e)[:120]


def _shape(obj, key="", depth=0):
    """Field names and types; keeps only short, harmless TFT-looking values."""
    if depth > 6:
        return "…"
    if isinstance(obj, dict):
        return {k: _shape(v, k, depth + 1) for k, v in list(obj.items())[:60]}
    if isinstance(obj, list):
        return [_shape(obj[0], key, depth + 1), f"len={len(obj)}"] if obj else []
    if isinstance(obj, str):
        if (len(obj) <= 40 and not UUID.match(obj) and TFT_HINT.search(key)
                and not SKIP.search(key) and not re.search(r"name|tag|id$|pid|uuid|note|body|msg", key, re.I)):
            return obj
        return f"str({len(obj)})"
    if isinstance(obj, (int, float, bool)) and TFT_HINT.search(key) and not SKIP.search(key):
        return obj
    return type(obj).__name__


def _camel_to_path(name):
    """'GetProductSessionV1ExternalSessions' -> '/product-session/v1/external-sessions'."""
    m = re.match(r"^Get([A-Z][A-Za-z]*?)V(\d+)([A-Z][A-Za-z]*)$", name)
    if not m:
        return None
    kebab = lambda s: re.sub(r"(?<!^)(?=[A-Z])", "-", s).lower()
    return f"/{kebab(m.group(1))}/v{m.group(2)}/{kebab(m.group(3))}"


def run(out_dir=HERE):
    report = {"note": "shapes only; values redacted except short TFT-related ones"}
    try:
        port, auth = _auth()
    except (OSError, IndexError) as e:
        print(f"Couldn't read the Riot Client lockfile: {e!r}. Is the Riot Client open?")
        return

    # 1) Ask the API to describe itself.
    paths, discovery = set(), {}
    for probe in ("/help", "/help?format=Full", "/swagger/v3/openapi.json", "/swagger/v2/swagger.json"):
        status, body = _get(port, auth, probe, timeout=10)
        discovery[probe] = status
        if status != 200 or not isinstance(body, dict):
            continue
        if "paths" in body:  # OpenAPI / Swagger
            for p, ops in body["paths"].items():
                if "get" in ops and "{" not in p:
                    paths.add(p)
        funcs = body.get("functions")
        if isinstance(funcs, dict):
            report["help_function_count"] = len(funcs)
            for fname, info in funcs.items():
                url = info.get("url") if isinstance(info, dict) else None
                method = (info.get("http_method") or info.get("method") or "") if isinstance(info, dict) else ""
                if url and method.upper() == "GET" and "{" not in url:
                    paths.add(url)
                elif fname.startswith("Get") and "By" not in fname:
                    p = _camel_to_path(fname)
                    if p:
                        paths.add(p)
            report["help_function_names"] = sorted(funcs)[:1500]
        events = body.get("events")
        if isinstance(events, dict):
            report["help_event_names"] = sorted(events)[:800]
    report["discovery"] = discovery
    if not paths:
        paths = set(FALLBACK_PATHS)
        report["used_fallback_list"] = True

    # 2) GET each safe, parameter-free endpoint and record its shape.
    results, skipped = {}, []
    for p in sorted(paths):
        if SKIP.search(p):
            skipped.append(p)
            continue
        status, body = _get(port, auth, p)
        if status == 200:
            results[p] = _shape(body)
        else:
            results[p] = {"status": status}
    report["endpoints_ok"] = {p: v for p, v in results.items() if not (isinstance(v, dict) and "status" in v and len(v) == 1)}
    report["endpoints_failed"] = {p: v["status"] for p, v in results.items()
                                  if isinstance(v, dict) and "status" in v and len(v) == 1}
    report["skipped_for_safety"] = skipped

    path = os.path.join(out_dir, "explore_output.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)
    print(f"Discovery: {discovery}")
    print(f"Endpoints answered: {len(report['endpoints_ok'])}, failed: {len(report['endpoints_failed'])}, "
          f"skipped for safety: {len(skipped)}")
    print(f"Saved to {path}")
