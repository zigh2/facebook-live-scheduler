"""Command line interface: fblive <command>."""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import signal
import sys
import threading
from datetime import timedelta
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, FixedStream, load_config, resolve_media
from .daemon import now_local, run_daemon
from .engine_obs import ObsEngine
from .facebook import FacebookError, FacebookLive
from .notify import Notifier
from .runner import run_stream
from .schedule import PlannedStream, expand_templates, plan_range, read_queue
from .state import RunState

log = logging.getLogger("fblive")


def setup_logging(cfg: Config | None, verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    if cfg is not None:
        try:
            cfg.log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(cfg.log_dir / "fblive.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError as e:
            print(f"warning: cannot write log file: {e}", file=sys.stderr)
    logging.getLogger("websocket").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def build(cfg: Config):
    fb = FacebookLive(cfg.facebook.page_id, cfg.facebook.token, cfg.facebook.graph_version, cfg.facebook.base_url)
    return ObsEngine(cfg.obs), fb, Notifier(cfg.telegram_token, cfg.telegram_chat_id), RunState(cfg.state_dir / "runs.json")


def _stop_event() -> threading.Event:
    ev = threading.Event()
    def handler(signum, frame):  # noqa: ARG001
        log.info("stop requested")
        ev.set()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, handler)
    return ev


def cmd_run(cfg: Config, args) -> int:
    _require_facebook(cfg)
    engine, fb, notifier, state = build(cfg)
    stop = _stop_event()
    notifier.send("🟢 بدأ برنامج الجدولة")
    try:
        run_daemon(cfg, engine, fb, notifier, state, stop)
    finally:
        engine.stop()
        engine.c.close()
    return 0


def cmd_plan(cfg: Config, args) -> int:
    rows, errors = read_queue(cfg.queue_csv)
    for e in errors:
        print("WARNING:", e)
    planned = plan_range(cfg, now_local(cfg).date(), args.days, rows)
    state = RunState(cfg.state_dir / "runs.json")
    if not planned:
        print("Nothing planned.")
    for p in planned:
        st = state.status(p.key) or "-"
        missing = "" if Path(p.file).is_file() else "   <-- FILE NOT FOUND"
        print(f"{p.start:%Y-%m-%d %H:%M}  {p.name:<10} [{st:<8}] {p.title}  <- {p.file}{missing}")
    return 0


def cmd_check(cfg: Config, args) -> int:
    problems = 0
    def line(ok: bool, text: str) -> None:
        nonlocal problems
        print(("[ OK ] " if ok else "[FAIL] ") + text)
        problems += 0 if ok else 1
    line(bool(cfg.facebook.page_id), "FB_PAGE_ID set")
    line(bool(cfg.facebook.token), "FB_PAGE_TOKEN set")
    if cfg.facebook.page_id and cfg.facebook.token:
        try:
            me = FacebookLive(cfg.facebook.page_id, cfg.facebook.token, cfg.facebook.graph_version, cfg.facebook.base_url).whoami()
            line(str(me.get("id")) == cfg.facebook.page_id, f"token belongs to: {me.get('name')} (id {me.get('id')})")
        except FacebookError as e:
            line(False, f"Facebook token check: {e}")
    engine = ObsEngine(cfg.obs)
    try:
        engine.connect()
        line(True, "connected to OBS WebSocket")
        for w in engine.ensure_setup():
            line(False, w)
    except Exception as e:  # noqa: BLE001
        line(False, f"OBS: {e}")
    line(probe_available(cfg), "ffprobe available (media pre-checks)")
    rows, errors = read_queue(cfg.queue_csv)
    for e in errors:
        line(False, e)
    planned = plan_range(cfg, now_local(cfg).date(), 7, rows)
    for p in planned:
        if not Path(p.file).is_file():
            line(False, f"{p.start:%Y-%m-%d %H:%M} {p.name}: file not found: {p.file}")
    print(f"\n{len(planned)} stream(s) planned in the next 7 days; {problems} problem(s).")
    return 1 if problems else 0


def probe_available(cfg: Config) -> bool:
    import shutil
    return bool(shutil.which(cfg.ffprobe) or Path(cfg.ffprobe).is_file())


def cmd_now(cfg: Config, args) -> int:
    _require_facebook(cfg)
    now = now_local(cfg)
    if args.stream:
        fixed = next((s for s in cfg.fixed if s.name == args.stream), None)
        if not fixed:
            print(f"no fixed stream named '{args.stream}'. Available: {', '.join(s.name for s in cfg.fixed)}")
            return 2
        p = PlannedStream(f"manual|{now:%Y%m%d%H%M}", fixed.name, now, expand_templates(fixed.title, now.date()),
                          expand_templates(fixed.description, now.date()), str(resolve_media(cfg, fixed.file)), "fixed")
    else:
        if not (args.file and args.title):
            print("use --stream NAME, or --file and --title")
            return 2
        p = PlannedStream(f"manual|{now:%Y%m%d%H%M}", "manual", now, args.title, args.description or "",
                          str(resolve_media(cfg, args.file)), "queue")
    engine, fb, notifier, _ = build(cfg)
    stop = _stop_event()
    if args.test:
        print("TEST MODE: the broadcast is created as UNPUBLISHED (not visible on the Page).")
    try:
        out = run_stream(cfg, p, engine, fb, notifier, test=args.test, stop=stop)
    finally:
        engine.c.close()
    print(f"result: {out.status} {out.detail}")
    return 0 if out.status in ("ok", "stopped") else 1


def cmd_obs_setup(cfg: Config, args) -> int:
    engine = ObsEngine(cfg.obs)
    engine.connect()
    for w in engine.ensure_setup():
        print("WARNING:", w)
    print(f"OBS is ready: scenes '{cfg.obs.scene}' / '{cfg.obs.idle_scene}' and source '{cfg.obs.input_name}' exist.")
    engine.c.close()
    return 0


def cmd_setup_token(cfg: Config, args) -> int:
    from .setup_token import run_setup
    return run_setup(cfg.base_dir / ".env", cfg.facebook.graph_version, cfg.facebook.base_url)


def _require_facebook(cfg: Config) -> None:
    if not (cfg.facebook.page_id and cfg.facebook.token):
        raise ConfigError("FB_PAGE_ID / FB_PAGE_TOKEN missing - run: fblive setup-token")


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fblive", description="Scheduled Facebook Live broadcasts through OBS")
    ap.add_argument("--version", action="version", version=f"fblive {__version__}")
    ap.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the scheduler (keep it running)").set_defaults(fn=cmd_run)
    p = sub.add_parser("plan", help="show upcoming streams")
    p.add_argument("--days", type=int, default=3)
    p.set_defaults(fn=cmd_plan)
    sub.add_parser("check", help="verify token, OBS, files").set_defaults(fn=cmd_check)
    sub.add_parser("obs-setup", help="create the OBS scenes/source").set_defaults(fn=cmd_obs_setup)
    sub.add_parser("setup-token", help="get a long-lived Page token").set_defaults(fn=cmd_setup_token)
    p = sub.add_parser("now", help="broadcast something right now")
    p.add_argument("--stream", help="name of a fixed stream from config.yaml")
    p.add_argument("--file")
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--test", action="store_true", help="create as UNPUBLISHED (not public)")
    p.set_defaults(fn=cmd_now)
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    setup_logging(cfg, args.verbose)
    try:
        return args.fn(cfg, args)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        log.exception("fatal: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
