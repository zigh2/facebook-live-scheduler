"""Runs ONE broadcast end to end: preflight -> create live -> stream -> monitor -> cleanup."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .engine import Engine, EngineError
from .facebook import FacebookError, FacebookLive, split_stream_url
from .notify import Notifier
from .probe import ProbeError, probe
from .schedule import PlannedStream

log = logging.getLogger(__name__)


@dataclass
class Outcome:
    status: str  # ok | stopped | timeout | interrupted | failed
    detail: str = ""


def _fmt(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def run_stream(cfg: Config, p: PlannedStream, engine: Engine, fb: FacebookLive, notifier: Notifier,
               test: bool = False, stop: threading.Event | None = None) -> Outcome:
    stop = stop or threading.Event()
    label = f"[{p.name}] {p.title}"
    media = Path(p.file)

    # ---- preflight (no retries: these cannot fix themselves) ----
    if not media.is_file():
        return _fail(notifier, label, f"media file not found: {media}")
    duration = None
    try:
        info = probe(media, cfg.ffprobe)
    except ProbeError as e:
        return _fail(notifier, label, str(e))
    if info is None:
        log.warning("ffprobe not found - skipping media checks (install FFmpeg to enable them)")
    else:
        if not info.has_video:
            return _fail(notifier, label, f"{media.name} has no video stream")
        if not info.has_audio:
            return _fail(notifier, label, f"{media.name} has no audio stream - Facebook Live requires audio")
        duration = info.duration
        log.info("media: %s %dx%d, %s", info.vcodec, info.width, info.height, _fmt(duration) if duration else "unknown duration")
    max_seconds = duration * 1.05 + 120 if duration else float(cfg.max_stream_seconds)

    attempt = 0
    while True:
        attempt += 1
        live = None
        started = False
        t0 = 0.0
        try:
            engine.connect()
            for w in engine.prepare(media):
                log.warning("engine: %s", w)
            live = fb.create_live(p.title, p.description, "UNPUBLISHED" if test else "LIVE_NOW")
            log.info("created live video %s%s", live.id, " (TEST: unpublished)" if test else "")
            server, key = split_stream_url(live.secure_stream_url)
            engine.start(server, key)
            started = True
            t0 = time.monotonic()
            link = ""
            try:
                link = fb.get_live(live.id).get("permalink_url", "") or ""
            except FacebookError:
                pass
            notifier.send(f"🔴 بدأ البث: {p.title}" + (" (تجريبي)" if test else "") + (f"\nhttps://facebook.com{link}" if link.startswith("/") else ""))
            reason, detail = _monitor(cfg, engine, stop, max_seconds)
            stats = engine.stop()
            _end_live(fb, live)
            live = None
            elapsed = time.monotonic() - t0
            msg = f"{reason}, {_fmt(elapsed)}, frames={stats.get('frames')} skipped={stats.get('skipped')} {detail}".strip()
            log.info("broadcast ended: %s", msg)
            if reason == "finished":
                notifier.send(f"✅ انتهى البث: {p.title} ({_fmt(elapsed)})")
                return Outcome("ok", msg)
            if reason == "stopped":
                return Outcome("stopped", msg)
            notifier.send(f"⚠️ البث انتهى بشكل غير طبيعي ({reason}): {p.title}\n{detail}")
            return Outcome("timeout" if reason == "timeout" else "interrupted", msg)
        except (EngineError, FacebookError) as e:
            retryable = getattr(e, "retryable", True) and not started
            log.error("attempt %d failed: %s", attempt, e)
            _cleanup(engine, fb, live)
            if started:
                notifier.send(f"⚠️ توقف البث أثناء التشغيل: {p.title}\n{e}")
                return Outcome("interrupted", str(e))
            if retryable and attempt <= cfg.retries and not stop.is_set():
                log.info("retrying in %.0fs (attempt %d/%d)", cfg.retry_delay, attempt, cfg.retries + 1)
                if stop.wait(cfg.retry_delay):
                    return Outcome("stopped", "stopped while waiting to retry")
                continue
            return _fail(notifier, label, str(e))


def _monitor(cfg: Config, engine: Engine, stop: threading.Event, max_seconds: float) -> tuple[str, str]:
    t0 = time.monotonic()
    errors = 0
    detail = ""
    while True:
        if stop.is_set():
            return "stopped", "stopped by user"
        try:
            st = engine.poll()
            errors = 0
            detail = st.detail
            if st.finished:
                return "finished", ""
            if not st.streaming:
                return "dropped", "OBS stopped streaming (connection to Facebook lost?)"
        except EngineError as e:
            errors += 1
            detail = str(e)
            if not e.retryable or errors >= 5:
                return "dropped", detail
        if time.monotonic() - t0 > max_seconds:
            return "timeout", f"exceeded {_fmt(max_seconds)}"
        stop.wait(cfg.poll_interval)


def _end_live(fb: FacebookLive, live) -> None:
    try:
        fb.end_live(live.id)
    except FacebookError as e:
        log.warning("could not end live video %s: %s", live.id, e)


def _cleanup(engine: Engine, fb: FacebookLive, live) -> None:
    try:
        engine.stop()
    except Exception as e:  # noqa: BLE001
        log.warning("cleanup: engine stop failed: %s", e)
    if live is not None:
        _end_live(fb, live)


def _fail(notifier: Notifier, label: str, why: str) -> Outcome:
    log.error("%s FAILED: %s", label, why)
    notifier.send(f"❌ فشل البث: {label}\n{why}")
    return Outcome("failed", why)
