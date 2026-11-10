"""Follows TFT's own log file (AppData\\Local\\TFT\\Saved\\Logs\\TFT.log) to learn
what you're doing: lobby, queue, match found, in game, game over, plus the
queue you picked and a stage estimate from carousel rounds.

Log lines this relies on (as written by the standalone TFT client, Nov 2026):
  RGIOPRms: ... 'teambuilder/v1/tbdGameDtoV1/tft' ... payload '{"counter":0,"phaseName":"MATCHMAKING","queueId":1100,...
  RGIOPRms: ... 'teambuilder/v1/removedFromServiceV1' ...         (left queue / declined)
  product-session: Session for teamfighttactics.live updated phase from Idle to Gameplay
  TFTLobbySubsystem: Entering LobbyState[ETFTLobbyState::EndOfGameSummary]
  TFTLobbySubsystem: Setting end of game config: ActiveQueueId=[1100] ...
  ... Phase[ETFTPhaseType::DraftDeparture]                         (a carousel just ended)
"""

import calendar
import json
import os
import re
import time

LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "TFT", "Saved", "Logs")

_TS = re.compile(r"^\[(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2}):(\d{3})\]")
_TB = re.compile(r"RGIOPRms: .*tbdGameDtoV1/tft.*payload '\{\"counter\":\d+,\"phaseName\":\"(\w+)\",\"queueId\":(\d+)")
_REMOVED = re.compile(r"RGIOPRms: .*removedFromServiceV1")
_SESSION = re.compile(r"Session for teamfighttactics\.\w+ updated phase from (\w+) to (\w+)")
_LOBBY = re.compile(r"Entering LobbyState\[ETFTLobbyState::(\w+)\]")
_QUEUE = re.compile(r"ActiveQueueId=\[(\d+)\]")
_CAROUSEL = re.compile(r"Phase\[ETFTPhaseType::DraftDeparture\]")
_PARTY = re.compile(r"RGIOPRms: .*resource 'parties/v1/notifications/tft' .*payload '(.*)'\s*$")

TB_PHASES = {"MATCHMAKING": "Matchmaking", "AFK_CHECK": "ReadyCheck", "CHAMPION_SELECT": "GameStart"}
MAX_INITIAL_READ = 30 * 1024 * 1024  # on startup, only replay the last 30 MB


def _ts(line):
    m = _TS.match(line)
    if not m:
        return None
    y, mo, d, h, mi, s, ms = map(int, m.groups())
    return calendar.timegm((y, mo, d, h, mi, s)) + ms / 1000  # log times are UTC


def ordinal(n):
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


class LogWatcher:
    def __init__(self, log_dir=LOG_DIR):
        self.path = os.path.join(log_dir, "TFT.log")
        self.eog_path = os.path.join(log_dir, "TFTEoGStats.json")
        self._offset = 0
        self._started = False
        self._buf = ""
        self.reset()

    def reset(self):
        self.phase = "Menu"  # main menu until a lobby state says otherwise
        self.phase_since = None
        self.queue_id = None
        self.carousels = 0
        self.game_started_at = None
        self.ended_at = None
        self.last_line_at = None
        self.party_size = None   # players in your party (leader + members)
        self.party_max = None
        self.party_id = None

    # ---- reading ----
    def poll(self):
        """Read any new lines. Returns False if the log doesn't exist."""
        try:
            st = os.stat(self.path)
        except OSError:
            return False
        if not self._started:
            # First run: replay recent history so we know the current state.
            self._started = True
            self._offset = max(0, st.st_size - MAX_INITIAL_READ)
        elif st.st_size < self._offset:
            # The client restarted and began a fresh TFT.log.
            self.reset()
            self._offset = 0
            self._buf = ""
        if st.st_size == self._offset:
            return True
        with open(self.path, "rb") as f:
            f.seek(self._offset)
            chunk = f.read(st.st_size - self._offset)
        self._offset = st.st_size
        text = self._buf + chunk.decode("utf-8", "replace")
        lines = text.split("\n")
        self._buf = lines.pop()  # keep a partial last line for next time
        for line in lines:
            self.feed(line)
        return True

    def feed(self, line):
        t = _ts(line)
        if t:
            self.last_line_at = t
        m = _PARTY.search(line)
        if m:
            self._party(m.group(1))
            return
        m = _TB.search(line)
        if m:
            self._set(TB_PHASES.get(m.group(1), self.phase), t)
            self.queue_id = int(m.group(2))
            return
        if _REMOVED.search(line):
            if self.phase in ("Matchmaking", "ReadyCheck"):
                self._set("Lobby", t)
            return
        m = _SESSION.search(line)
        if m:
            old, new = m.groups()
            if new == "Gameplay":
                self._set("InProgress", t)
                self.game_started_at = t
                self.carousels = 0
            elif old == "Gameplay":
                self._set("EndOfGame", t)
                self.ended_at = t
            return
        m = _LOBBY.search(line)
        if m:
            state = m.group(1)
            if state == "Matchmaking" and self.phase not in ("Matchmaking", "ReadyCheck"):
                self._set("Matchmaking", t)
            elif state == "Frontend" and self.phase in ("Menu", "Lobby", "EndOfGame"):
                self._set("Menu", t)  # the main menu / home screen
            elif state in ("SoloMode", "SocialMode") and self.phase in ("Menu", "Lobby", "EndOfGame"):
                self._set("Lobby", t)  # an actual lobby with a mode picked
            elif state == "InGame" and self.phase != "InProgress":
                self._set("InProgress", t)
                self.game_started_at = self.game_started_at or t
            return
        m = _QUEUE.search(line)
        if m:
            self.queue_id = int(m.group(1))
            return
        if self.phase == "InProgress" and _CAROUSEL.search(line):
            self.carousels += 1

    def _party(self, payload):
        """Parse a party update. Only counts and the party id are kept; the
        message also carries account ids and a chat token, which are ignored."""
        try:
            party = (json.loads(payload).get("player") or {}).get("currentParty") or {}
        except ValueError:
            return
        players = party.get("players") or []
        self.party_size = sum(1 for p in players if p.get("role") in ("LEADER", "MEMBER")) or None
        self.party_max = party.get("maxPartySize")
        self.party_id = party.get("partyId")

    def _set(self, phase, t):
        if phase != self.phase:
            self.phase = phase
            self.phase_since = t

    # ---- derived info ----
    def stage_number(self):
        """Carousels happen at 1-1, 2-4, 3-4, ... so after the Nth carousel you're
        in stage N (for N >= 2). Returns None when it can't tell."""
        if self.phase != "InProgress" or self.carousels < 2:
            return None
        return self.carousels

    def placement(self, my_puuid=None):
        """Your finish from the end-of-game stats file, if it belongs to the last game."""
        try:
            if self.ended_at and os.path.getmtime(self.eog_path) < self.ended_at - 600:
                return None  # stats file is from an older game
            with open(self.eog_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        players = (data.get("statsBlock") or {}).get("players") or []
        me = next((p for p in players if my_puuid and p.get("PUUID") == my_puuid), None)
        if me is None and len(players) == 1:
            me = players[0]
        if not me or not me.get("ffaStanding"):
            return None
        standing = int(me["ffaStanding"])
        if data.get("queueId") == 1160:  # Double Up: two players per team
            return ordinal((standing + 1) // 2)  # team placement
        return ordinal(standing)

    def session(self, my_puuid=None):
        """What you're doing in TFT right now, or None if there's no log yet."""
        if not self.poll():
            return None
        out = {
            "phase": self.phase,
            "queue_id": self.queue_id,
            "since": self.phase_since,
            "game_started_at": self.game_started_at,
            "stage_number": self.stage_number(),
            "placement": None,
            "party_size": self.party_size,
            "party_max": self.party_max,
            "party_id": self.party_id,
        }
        if self.phase == "EndOfGame":
            out["placement"] = self.placement(my_puuid)
        return out
