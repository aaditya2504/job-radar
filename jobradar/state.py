"""Persistent run state: which jobs were already seen and their cached scores."""
import json
import os
import tempfile
from datetime import datetime, timezone


class State:
    def __init__(self, path):
        self.path = path
        self.first_run = not os.path.exists(path)
        self.data = {"seen": {}, "scores": {}}
        if not self.first_run:
            with open(path, encoding="utf-8") as f:
                self.data.update(json.load(f))

    def seen(self, key):
        return key in self.data["seen"]

    def mark_seen(self, key):
        self.data["seen"].setdefault(key, datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def cached_score(self, key):
        return self.data["scores"].get(key)

    def cache_score(self, key, score, reasons, engine="claude"):
        self.data["scores"][key] = {"score": score, "reasons": reasons, "engine": engine}

    def save(self):
        """Atomic write: a crash mid-save never leaves a corrupt state file."""
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)
