"""Persistent settings + watch list (JSON in XDG config dir)."""

import json
import os
from pathlib import Path

from gi.repository import GLib

PATH = Path(GLib.get_user_config_dir()) / "linchan" / "state.json"

DEFAULTS = {
    "board": "g",
    "hide_nsfw": False,
    "width": 1200,
    "height": 800,
    "watched": {},  # "board/no" -> {board,no,subject,seen,replies,dead}
}


class State:
    def __init__(self):
        self.data = json.loads(json.dumps(DEFAULTS))
        try:
            self.data.update(json.loads(PATH.read_text()))
        except (OSError, ValueError):
            pass

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v
        self.save()

    def save(self):
        try:
            PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1))
            os.replace(tmp, PATH)
        except OSError as e:
            print("could not save state:", e)
