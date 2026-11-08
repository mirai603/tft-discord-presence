"""Turns game state into the fields Discord shows. Kept free of I/O so it's easy to test."""

import hashlib
import time

from .riot_client import QUEUES

IN_GAME_PHASES = {"InProgress", "Reconnect"}
PHASE_TEXT = {
    "Menu": "In Main Menu",
    "Lobby": "In Lobby",
    "Matchmaking": "In Queue",
    "ReadyCheck": "Match Found",
    "ChampSelect": "Loading In",
    "GameStart": "Loading In",
    "WaitingForStats": "Game Over",
    "PreEndOfGame": "Game Over",
    "EndOfGame": "Game Over",
}


def _party_fields(session, show):
    """Discord shows party_size as "(2 of 8)" after the state line."""
    size, mx = (session or {}).get("party_size"), (session or {}).get("party_max")
    if not show.get("party", True) or not size or not mx or size > mx:
        return {}
    out = {"party_size": [int(size), int(mx)]}
    pid = (session or {}).get("party_id")
    if pid:  # Discord wants an id with a size; send a hash, not Riot's real party id
        out["party_id"] = hashlib.sha256(pid.encode()).hexdigest()[:32]
    return out


def build(cfg, session, game, stage, rank, game_started_at):
    """Return a dict of pypresence.update() kwargs, or None to clear presence.

    session: from LCU.session() (may be None if the client API isn't reachable)
    game:    from live_client.parse() (None when not in a match)
    """
    queue = (session or {}).get("queue_name") or QUEUES.get((session or {}).get("queue_id"))
    phase = (session or {}).get("phase")
    is_tft = (session or {}).get("is_tft")
    show = cfg.get("show", {})
    assets = cfg.get("assets", {})

    large_text = "Teamfight Tactics"
    if rank and show.get("rank", True):
        large_text = f"Teamfight Tactics · {rank}"

    out = {
        "large_image": assets.get("large_image", "tft_logo"),
        "large_text": large_text,
    }

    if not game and phase in IN_GAME_PHASES:
        # Standalone TFT client: we know you're in a match but may have no stats.
        game = {"health": None, "level": None, "gold": None, "players_alive": None}

    if game:
        details_parts = [queue or "TFT"]
        if show.get("stage", True):
            if stage:
                details_parts.append(f"Stage {stage}")
            elif (session or {}).get("stage_number"):
                details_parts.append(f"Stage {session['stage_number']}")  # from the log
        out["details"] = " · ".join(details_parts)

        state_parts = []
        if game["health"] is not None and show.get("health", True):
            state_parts.append(f"{game['health']} HP")
        if game["level"] is not None and show.get("level", True):
            state_parts.append(f"Lvl {game['level']}")
        if game["gold"] is not None and show.get("gold", False):
            state_parts.append(f"{game['gold']}g")
        if game["players_alive"] and show.get("players_left", True):
            state_parts.append(f"{game['players_alive']} left")
        progress = (session or {}).get("progress")
        if not state_parts and progress and progress != "InGame" and len(str(progress)) <= 40:
            state_parts.append(str(progress))  # whatever Riot shows friends mid-game
        out["state"] = " · ".join(state_parts) if state_parts else "In Game"

        out["start"] = int(game_started_at)
        if ((session or {}).get("party_size") or 0) > 1:
            out.update(_party_fields(session, show))
        small = assets.get("queue_icons", {}).get(queue or "", assets.get("small_image"))
        if small:
            out["small_image"] = small
            out["small_text"] = queue or "TFT"
        return out

    # Not in a match: show client status only if it's a TFT lobby/queue.
    if phase and phase in PHASE_TEXT and is_tft and show.get("lobby", True):
        out["details"] = PHASE_TEXT[phase]
        if phase == "Menu":
            return out  # no queue or party: you haven't picked a mode yet
        placement = (session or {}).get("placement")
        if phase == "EndOfGame" and placement:
            team = (session or {}).get("queue_id") == 1160
            out["details"] = f"{'Team finished' if team else 'Finished'} {placement}"
        if queue:
            out["state"] = queue
            if rank and queue in ("Ranked", "Double Up", "Hyper Roll") and show.get("rank", True):
                out["state"] = f"{queue} · {rank}"
        since = (session or {}).get("since")
        if phase == "Matchmaking" and since:
            out["start"] = int(since)  # Discord shows time spent in queue
        if phase in ("Lobby", "Matchmaking", "ReadyCheck"):
            out.update(_party_fields(session, show))
            if "party_size" in out and "state" not in out:
                out["state"] = "Party"  # Discord only shows the size next to a state line
        return out

    return None


def game_start_timestamp(game_time_seconds, now=None):
    """Discord shows 'elapsed' from this; derive it from the in-game clock."""
    return (now or time.time()) - (game_time_seconds or 0)
