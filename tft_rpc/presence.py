"""Turns game state into the fields Discord shows. Kept free of I/O so it's easy to test."""

import hashlib

from .riot_client import QUEUES
from .log_watcher import ordinal

PHASE_TEXT = {
    "Menu": "In Main Menu",
    "Lobby": "In Lobby",
    "Matchmaking": "In Queue",
    "ReadyCheck": "Match Found",
    "GameStart": "Loading In",
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

    session: from LogWatcher.session() (None when TFT isn't open)
    game:    {"health", "placement"} from the screen reader during a match, else None
    """
    queue = QUEUES.get((session or {}).get("queue_id"))
    phase = (session or {}).get("phase")
    show = cfg.get("show", {})
    assets = cfg.get("assets", {})

    large_text = "Teamfight Tactics"
    if rank and show.get("rank", True):
        large_text = f"Teamfight Tactics · {rank}"

    out = {
        "large_image": assets.get("large_image", "tft_logo"),
        "large_text": large_text,
    }

    if phase == "InProgress":
        game = game or {}
        details_parts = [queue or "TFT"]
        if show.get("stage", True):
            if stage:
                details_parts.append(f"Stage {stage}")
            elif (session or {}).get("stage_number"):
                details_parts.append(f"Stage {session['stage_number']}")  # from the log
        out["details"] = " · ".join(details_parts)

        state_parts = []
        if game.get("health") is not None and show.get("health", True):
            state_parts.append(f"{game['health']} HP")
        if game.get("placement") and show.get("placement", True):
            team = (session or {}).get("queue_id") == 1160
            state_parts.append(("Team " if team else "") + ordinal(game["placement"]))
        out["state"] = " · ".join(state_parts) if state_parts else "In Game"

        out["start"] = int(game_started_at)
        small = assets.get("queue_icons", {}).get(queue or "", assets.get("small_image"))
        if small:
            out["small_image"] = small
            out["small_text"] = queue or "TFT"
        return out

    # Not in a match: menu / lobby / queue / game over.
    if phase in PHASE_TEXT and show.get("lobby", True):
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

