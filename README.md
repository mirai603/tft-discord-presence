# TFT Discord Rich Presence

Shows what you're doing in Teamfight Tactics on your Discord profile, for the
standalone TFT client (Patch 18.6+, Nov 2026):

```
Playing Teamfight Tactics
Ranked · Stage 3-5
62 HP                         12:34 elapsed
```

Outside a match it shows **In Main Menu**, **In Lobby · Double Up (2 of 8)**,
**In Queue** with a queue timer, **Match Found**, and **Finished 3rd** after a game.
It runs in the system tray and can start with Windows.

## How it works

TFT's standalone client doesn't offer a local game API (the old port-2999
in-game API is gone), so the data comes from three places:

| Data | Source |
| --- | --- |
| Menu / lobby / queue / match found / in game / game over, queue type, party size, placement | TFT's own log file (`%LOCALAPPDATA%\TFT\Saved\Logs\TFT.log`) and end-of-game stats file |
| Stage number fallback (from carousel rounds) | TFT's log |
| Exact stage (e.g. `3-5`) and your HP | OCR on the TFT window (Tesseract) |
| TFT running / in game (fallback), Riot ID, region | Riot Client local API |
| Rank and LP (optional) | Riot's official API ([tft-league-v1](https://developer.riotgames.com/apis#tft-league-v1)), needs your own API key |

HP is found by locating your gold portrait ring in the player list on the right
and reading the HP box next to it, so it works wherever your row is.

It only shows **your own** information, after the fact, to people viewing your
profile. It doesn't track opponents or give in-game advice, in line with
[Riot's TFT developer policy](https://developer.riotgames.com/docs/tft).

## Setup (Windows)

1. **Python 3.9+** from [python.org](https://www.python.org/downloads/) (tick *Add to PATH*).
2. **Install dependencies** in a virtual environment:
   ```
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```
3. **Tesseract OCR** for stage and HP reading:
   [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki), default path.
4. **Discord application**: create one at the
   [Discord Developer Portal](https://discord.com/developers/applications) named
   *Teamfight Tactics*, upload a `tft_logo` image under *Rich Presence → Art Assets*.
5. **Config**: run the app once to create `config.json` from `config.example.json`,
   then paste your Discord **Application ID** into `discord_client_id`.
6. **Run it** in the tray (no console window), then right-click the icon and tick
   **Start with Windows**:
   ```
   .venv\Scripts\pythonw.exe -m tft_rpc tray
   ```

In Discord, turn on *Settings → Activity Privacy → Share your detected activities*.

## Commands

| Command | What it does |
| --- | --- |
| `python -m tft_rpc` | Run in the terminal (Ctrl+C to stop) |
| `python -m tft_rpc tray` | Run in the system tray |
| `python -m tft_rpc install-startup` / `uninstall-startup` | Start with Windows on/off |
| `python -m tft_rpc calibrate` | During a match: saves `calibrate_full.png` and prints the stage and HP it reads |
| `python -m tft_rpc dump` | Print what each data source currently reports |
| `python -m tft_rpc probe` / `explore` | Developer tools: inspect the Riot Client's local API (read-only; outputs are git-ignored because they can contain account details) |

## Configuration

`config.json` (git-ignored, created from `config.example.json`):

- `show.*`: turn individual fields on/off (`gold` is off by default).
- `stage_ocr.region`: where the stage text is, as `[left, top, width, height]`
  fractions of a 16:9 game view. Use `calibrate` to check it.
- `stage_ocr.capture`: `"window"` (default; works windowed, borderless or
  fullscreen) or `"monitor"`.
- `stage_ocr.only_when_focused`: only read the screen while TFT is the active window.
- `riot_api_key`: optional, for rank/LP. Development keys expire every 24 hours;
  a personal key can be requested on the Riot Developer Portal.

## Limitations

- Relies on TFT's log wording and UI layout, which can change between patches.
- HP/stage reading needs TFT visible and focused; values update every few seconds
  and Discord accepts an update at most every 15 seconds.
- Windows only.

---

TFT Discord Rich Presence isn't endorsed by Riot Games and doesn't reflect the
views or opinions of Riot Games or anyone officially involved in producing or
managing Riot Games properties. Riot Games, and all associated properties are
trademarks or registered trademarks of Riot Games, Inc.
