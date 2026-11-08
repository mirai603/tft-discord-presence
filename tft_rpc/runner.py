"""The main presence loop, usable from the terminal or from the tray icon.

It can be paused (presence cleared, nothing read) and stopped from another
thread, and reports a one-line status for the tray tooltip/menu.
"""

import asyncio
import threading
import time

from . import live_client, presence

# status kinds the tray turns into icon colours
DISCORD_WAIT, IDLE, ACTIVE, PAUSED = "discord", "idle", "active", "paused"


class PresenceRunner:
    def __init__(self, cfg, log=print, on_status=None):
        self.cfg = cfg
        self.log = log
        self.on_status = on_status or (lambda text, kind: None)
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self._status = None

    def _set_status(self, text, kind):
        if (text, kind) != self._status:
            self._status = (text, kind)
            self.on_status(text, kind)

    def stop(self):
        self.stop_event.set()

    def _sleep(self, seconds):
        """Sleep, but wake immediately when asked to stop. Returns True if stopping."""
        return self.stop_event.wait(seconds)

    def run(self):
        # Imported here so the module loads even if pypresence isn't installed yet.
        from pypresence import Presence
        from pypresence.exceptions import DiscordNotFound, InvalidPipe, PipeClosed
        from .factory import get_session, make_hp_reader, make_ranked, make_reader
        from .lcu import LCU
        from .log_watcher import LogWatcher
        from .riot_client import RiotClient

        cfg = self.cfg
        client_id = str(cfg.get("discord_client_id", "")).strip()
        if not client_id or client_id.startswith("PUT"):
            self.log("Set discord_client_id in config.json first (see README).")
            self._set_status("Missing Discord ID in config.json", DISCORD_WAIT)
            return

        # pypresence needs an asyncio loop; give this thread its own.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        lcu = LCU(cfg.get("lockfile_path") or None)
        rc = RiotClient()
        logw = LogWatcher()
        reader = make_reader(cfg)
        hp_reader = make_hp_reader(cfg)
        ranked = make_ranked(cfg, rc)
        poll = cfg.get("poll_seconds", 3)
        min_update = max(15, cfg.get("min_update_seconds", 15))  # Discord rate-limits updates

        rpc, connected = Presence(client_id, loop=loop), False
        last_payload, last_sent = None, 0.0
        started_at, last_phase = None, None
        was_paused = False

        self.log("TFT rich presence running.")
        while not self.stop_event.is_set():
            try:
                if self.paused.is_set():
                    if not was_paused and connected:
                        rpc.clear()
                        self.log("Paused (presence cleared).")
                    was_paused, last_payload = True, None
                    self._set_status("Paused", PAUSED)
                    if self._sleep(1):
                        break
                    continue
                if was_paused:
                    self.log("Resumed.")
                    was_paused = False

                if not connected:
                    try:
                        rpc.connect()
                        connected = True
                        self.log("Connected to Discord.")
                    except (DiscordNotFound, InvalidPipe, ConnectionRefusedError, FileNotFoundError) as e:
                        first = self._status != ("Waiting for Discord", DISCORD_WAIT)
                        self._set_status("Waiting for Discord", DISCORD_WAIT)
                        if first:
                            self.log(f"Waiting for Discord desktop app... ({type(e).__name__})")
                        if self._sleep(10):
                            break
                        continue

                session = get_session(rc, lcu, logw)
                game = live_client.parse(live_client.get_all_game_data())
                in_match = bool(game) or (session or {}).get("phase") in presence.IN_GAME_PHASES

                stage = None
                if in_match:
                    if started_at is None:
                        started_at = (session or {}).get("game_started_at") or \
                            presence.game_start_timestamp(game["game_time"] if game else 0)
                    stage = reader.update() if reader else None
                    log_stage = (session or {}).get("stage_number")
                    if stage and log_stage and int(stage.split("-")[0]) != log_stage:
                        stage = None  # OCR disagrees with the log; fall back to "Stage N"
                    hp = hp_reader.update() if hp_reader else None
                    place = hp_reader.placement if hp_reader else None
                    if hp is not None or place is not None:
                        if game is None:
                            game = {"health": None, "level": None, "gold": None, "players_alive": None}
                        if hp is not None:
                            game["health"] = hp
                        game["placement"] = place
                else:
                    started_at = None
                    if reader:
                        reader.reset()
                    if hp_reader:
                        hp_reader.reset()

                phase = (session or {}).get("phase")
                if ranked:
                    if phase == "EndOfGame" and last_phase != "EndOfGame":
                        ranked.game_ended()  # LP changed; refetch shortly
                    ranked.refresh()
                last_phase = phase
                rank_text = ranked.text((session or {}).get("queue_id")) if ranked else None

                payload = presence.build(cfg, session, game, stage, rank_text, started_at or time.time())

                now = time.time()
                if payload != last_payload and now - last_sent >= min_update:
                    if payload:
                        rpc.update(**payload)
                        self.log(f"→ {payload.get('details', '')} | {payload.get('state', '')}")
                    else:
                        rpc.clear()
                        self.log("→ (cleared)")
                    last_payload, last_sent = payload, now

                if payload:  # what we're showing (or about to, once Discord's rate limit allows)
                    line = " · ".join(x for x in (payload.get("details"), payload.get("state")) if x)
                    self._set_status(line, ACTIVE)
                else:
                    self._set_status("Waiting for TFT", IDLE)

                if self._sleep(poll):
                    break
            except (PipeClosed, BrokenPipeError, ConnectionResetError):
                self.log("Lost Discord connection; retrying…")
                connected, last_payload = False, None
                rpc = Presence(client_id, loop=loop)
                if self._sleep(5):
                    break
            except Exception as e:  # keep running in the background no matter what
                self.log(f"Error: {e!r}")
                if self._sleep(5):
                    break

        try:
            if connected:
                rpc.clear()
                rpc.close()
        except Exception:
            pass
        self.log("Stopped.")
