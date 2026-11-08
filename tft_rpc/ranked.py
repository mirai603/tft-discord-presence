"""Your TFT rank and LP from Riot's official API (needs a key from
https://developer.riotgames.com). Neither TFT's log nor the Riot Client
expose LP locally: the client fetches it but never logs the response.

Endpoints used:
  account-v1  /riot/account/v1/accounts/by-riot-id/{name}/{tag}   -> puuid
  tft-league-v1  /tft/league/v1/by-puuid/{puuid}                  -> tier/LP per queue
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

# Platform (where your account lives) -> regional host for account-v1.
REGIONAL = {
    "NA1": "americas", "BR1": "americas", "LA1": "americas", "LA2": "americas",
    "EUW1": "europe", "EUN1": "europe", "TR1": "europe", "RU": "europe", "ME1": "europe",
    "KR": "asia", "JP1": "asia", "OC1": "asia", "SG2": "asia", "TW2": "asia", "VN2": "asia",
}

# Queue id you're in -> ranked queue to show.
QUEUE_TYPES = {1100: "RANKED_TFT", 1160: "RANKED_TFT_DOUBLE_UP", 1130: "RANKED_TFT_TURBO"}
APEX = {"MASTER", "GRANDMASTER", "CHALLENGER"}


class Ranked:
    REFRESH_SECONDS = 15 * 60
    AFTER_GAME_DELAY = 45  # LP updates a little after the game ends

    def __init__(self, api_key, platform="NA1", riot_id=None):
        self.key = (api_key or "").strip()
        self.platform = (platform or "NA1").upper()
        self.riot_id = riot_id
        self.puuid = None
        self.entries = {}
        self._next_fetch = 0.0
        self._warned = False

    @property
    def enabled(self):
        return bool(self.key) and not self.key.startswith("PUT")

    def _get(self, host, path):
        req = urllib.request.Request(f"https://{host}.api.riotgames.com{path}",
                                     headers={"X-Riot-Token": self.key})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def _resolve_puuid(self):
        if self.puuid or not self.riot_id or "#" not in self.riot_id:
            return self.puuid
        name, tag = self.riot_id.rsplit("#", 1)
        region = REGIONAL.get(self.platform, "americas")
        data = self._get(region, "/riot/account/v1/accounts/by-riot-id/"
                         f"{urllib.parse.quote(name)}/{urllib.parse.quote(tag)}")
        self.puuid = data.get("puuid")
        return self.puuid

    def game_ended(self):
        """Call when a match ends; refetch shortly after so new LP shows."""
        self._next_fetch = min(self._next_fetch, time.time() + self.AFTER_GAME_DELAY)

    def refresh(self):
        if not self.enabled or time.time() < self._next_fetch:
            return
        self._next_fetch = time.time() + self.REFRESH_SECONDS
        try:
            puuid = self._resolve_puuid()
            if not puuid:
                if not self._warned:
                    print("[rank] Set riot_id (e.g. \"Name#TAG\") in config.json to show LP.")
                    self._warned = True
                return
            rows = self._get(self.platform.lower(), f"/tft/league/v1/by-puuid/{puuid}")
            self.entries = {r.get("queueType"): r for r in rows if isinstance(r, dict)}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                print("[rank] Riot API key rejected. Development keys expire every 24 hours; "
                      "paste a fresh one into config.json (riot_api_key).")
                self._next_fetch = time.time() + 3600
            elif e.code == 429:
                self._next_fetch = time.time() + 120
            else:
                print(f"[rank] Riot API error {e.code}")
        except (OSError, ValueError) as e:
            print(f"[rank] Couldn't reach Riot API: {e}")
            self._next_fetch = time.time() + 300

    def text(self, queue_id=None):
        """e.g. 'Gold II · 45 LP' for the queue you're in (default: Ranked)."""
        qtype = QUEUE_TYPES.get(queue_id, "RANKED_TFT")
        e = self.entries.get(qtype) or self.entries.get("RANKED_TFT")
        if not e:
            return None
        if e.get("queueType") == "RANKED_TFT_TURBO":  # Hyper Roll uses rated tiers
            tier, rating = e.get("ratedTier"), e.get("ratedRating")
            return f"{str(tier).title()} · {rating}" if tier else None
        tier = str(e.get("tier", "")).title()
        if not tier:
            return None
        div = "" if tier.upper() in APEX else f" {e.get('rank', '')}"
        return f"{tier}{div} · {e.get('leaguePoints', 0)} LP"
