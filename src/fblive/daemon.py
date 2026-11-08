"""Long-running scheduler loop."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from .config import Config
from .engine import Engine
from .facebook import FacebookLive
from .notify import Notifier
from .runner import run_stream
from .schedule import QueueRow, due_streams, expired_streams, plan_range, read_queue
from .state import RunState

log = logging.getLogger(__name__)


def now_local(cfg: Config) -> datetime:
    if cfg.timezone:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(cfg.timezone)).replace(tzinfo=None)
    return datetime.now()


class QueueCache:
    """Re-reads the CSV only when it changes, so you can edit it while the daemon runs."""

    def __init__(self, path):
        self.path = path
        self._mtime = None
        self.rows: list[QueueRow] = []

    def get(self) -> list[QueueRow]:
        try:
            m = self.path.stat().st_mtime if self.path and self.path.is_file() else None
        except OSError:
            m = None
        if m != self._mtime:
            self._mtime = m
            self.rows, errors = read_queue(self.path)
            for e in errors:
                log.warning("%s", e)
            log.info("queue loaded: %d row(s)", len(self.rows))
        return self.rows


def run_daemon(cfg: Config, engine: Engine, fb: FacebookLive, notifier: Notifier, state: RunState,
               stop: threading.Event) -> None:
    grace = timedelta(minutes=cfg.grace_minutes)
    queue = QueueCache(cfg.queue_csv)
    log.info("scheduler started (grace %d min). Press Ctrl+C to stop.", cfg.grace_minutes)
    while not stop.is_set():
        now = now_local(cfg)
        planned = plan_range(cfg, now.date() - timedelta(days=1), 2, queue.get())
        due = due_streams(planned, now, grace, state.done_keys())
        if due:
            p = due[0]
            log.info("starting %s at %s", p.key, now.strftime("%H:%M:%S"))
            state.record(p.key, "started")
            outcome = run_stream(cfg, p, engine, fb, notifier, stop=stop)
            state.record(p.key, outcome.status, outcome.detail)
            continue
        for p in expired_streams(planned, now, grace, state.done_keys()):
            state.record(p.key, "missed", "grace window passed before it could start")
            log.warning("missed %s (daemon was busy or not running)", p.key)
            if now - (p.start + grace) < timedelta(minutes=5):
                notifier.send(f"⚠️ فاتَ موعد البث: {p.title} ({p.start:%H:%M})")
        stop.wait(cfg.loop_interval)
    log.info("scheduler stopped")
