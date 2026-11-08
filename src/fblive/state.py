"""Tiny JSON state file so a stream is never broadcast twice after a restart."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path


class RunState:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {}
        if self.path.is_file():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    def done_keys(self) -> set:
        return set(self.data)

    def status(self, key: str) -> str | None:
        return (self.data.get(key) or {}).get("status")

    def record(self, key: str, status: str, detail: str = "") -> None:
        self.data[key] = {"status": status, "detail": detail, "at": datetime.now().isoformat(timespec="seconds")}
        cutoff = datetime.now() - timedelta(days=60)
        self.data = {k: v for k, v in self.data.items() if _parse(v.get("at")) >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)


def _parse(s) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return datetime.now()
