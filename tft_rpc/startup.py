"""Adds/removes a shortcut in your Windows Startup folder that launches the tray
app (no console window) when you log in."""

import os
import subprocess
import sys

from .factory import HERE

SHORTCUT_NAME = "TFT Rich Presence.lnk"


def shortcut_path():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup", SHORTCUT_NAME)


def pythonw_path():
    """pythonw.exe next to the Python running us (your venv's), so no console opens."""
    exe_dir = os.path.dirname(sys.executable)
    candidate = os.path.join(exe_dir, "pythonw.exe")
    return candidate if os.path.isfile(candidate) else sys.executable


def is_installed():
    return os.path.isfile(shortcut_path())


def _ps_quote(s):
    return "'" + s.replace("'", "''") + "'"


def install():
    if sys.platform != "win32":
        return "Startup shortcut is only supported on Windows."
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut({lnk});"
        "$s.TargetPath = {exe}; $s.Arguments = '-m tft_rpc tray';"
        "$s.WorkingDirectory = {cwd}; $s.Description = 'TFT Discord Rich Presence';"
        "$s.Save()"
    ).format(lnk=_ps_quote(shortcut_path()), exe=_ps_quote(pythonw_path()), cwd=_ps_quote(HERE))
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0 or not is_installed():
        return f"Couldn't create the startup shortcut: {result.stderr.strip() or result.stdout.strip()}"
    return f"Will start with Windows: {shortcut_path()}"


def uninstall():
    try:
        os.remove(shortcut_path())
        return "Removed from Windows startup."
    except FileNotFoundError:
        return "It wasn't set to start with Windows."
