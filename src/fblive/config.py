"""Configuration loading (config.yaml + .env)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DAY_NAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


class ConfigError(Exception):
    pass


@dataclass
class FixedStream:
    name: str
    time: str
    file: str
    title: str
    description: str = ""
    days: tuple = ALL_DAYS


@dataclass
class ObsSettings:
    host: str = "localhost"
    port: int = 4455
    password: str = ""
    scene: str = "FBLive"
    idle_scene: str = "FBLive_Idle"
    input_name: str = "FBLive_Media"
    hw_decode: bool = True
    executable: str = ""
    launch_wait: float = 60.0
    start_delay: float = 3.0
    timeout: float = 10.0


@dataclass
class FacebookSettings:
    page_id: str = ""
    token: str = ""
    graph_version: str = "v26.0"
    base_url: str = "https://graph.facebook.com"


@dataclass
class Config:
    base_dir: Path
    engine: str = "obs"
    timezone: str | None = None
    grace_minutes: int = 10
    retries: int = 2
    retry_delay: float = 30.0
    poll_interval: float = 2.0
    loop_interval: float = 5.0
    max_stream_seconds: int = 4 * 3600
    media_dir: Path | None = None
    ffprobe: str = "ffprobe"
    queue_csv: Path | None = None
    queue_name: str = "daily"
    state_dir: Path = Path("state")
    log_dir: Path = Path("logs")
    telegram_token: str = ""
    telegram_chat_id: str = ""
    obs: ObsSettings = field(default_factory=ObsSettings)
    facebook: FacebookSettings = field(default_factory=FacebookSettings)
    fixed: list = field(default_factory=list)


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ (existing variables win)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _path(base: Path, value) -> Path | None:
    if not value:
        return None
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else (base / p)


def _days(value, where: str) -> tuple:
    if value in (None, "all", "ALL", "All"):
        return ALL_DAYS
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",")]
    out = []
    for v in value:
        k = str(v).strip().lower()[:3]
        if k not in DAY_NAMES:
            raise ConfigError(f"{where}: unknown day '{v}' (use mon,tue,wed,thu,fri,sat,sun or all)")
        out.append(DAY_NAMES[k])
    return tuple(sorted(set(out)))


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path} (copy config.example.yaml to config.yaml)")
    base = path.resolve().parent
    load_env_file(base / ".env")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}") from e

    cfg = Config(base_dir=base)
    cfg.engine = str(raw.get("engine", "obs")).lower()
    if cfg.engine != "obs":
        raise ConfigError(f"engine '{cfg.engine}' is not available yet (only 'obs')")
    cfg.timezone = raw.get("timezone") or None
    cfg.grace_minutes = int(raw.get("grace_minutes", cfg.grace_minutes))
    cfg.retries = int(raw.get("retries", cfg.retries))
    cfg.retry_delay = float(raw.get("retry_delay_seconds", cfg.retry_delay))
    cfg.max_stream_seconds = int(raw.get("max_stream_seconds", cfg.max_stream_seconds))
    cfg.media_dir = _path(base, raw.get("media_dir"))
    cfg.ffprobe = str(raw.get("ffprobe") or "ffprobe")
    cfg.state_dir = _path(base, raw.get("state_dir") or "state")
    cfg.log_dir = _path(base, raw.get("log_dir") or "logs")

    fb = raw.get("facebook") or {}
    cfg.facebook = FacebookSettings(
        page_id=str(os.environ.get("FB_PAGE_ID") or fb.get("page_id") or ""),
        token=os.environ.get("FB_PAGE_TOKEN", ""),
        graph_version=str(fb.get("graph_version") or "v26.0"),
        base_url=str(fb.get("base_url") or "https://graph.facebook.com").rstrip("/"),
    )

    o = raw.get("obs") or {}
    cfg.obs = ObsSettings(
        host=str(o.get("host", "localhost")),
        port=int(o.get("port", 4455)),
        password=os.environ.get("OBS_PASSWORD") or str(o.get("password") or ""),
        scene=str(o.get("scene", "FBLive")),
        idle_scene=str(o.get("idle_scene", "FBLive_Idle")),
        input_name=str(o.get("input_name", "FBLive_Media")),
        hw_decode=bool(o.get("hw_decode", True)),
        executable=str(o.get("executable") or ""),
        launch_wait=float(o.get("launch_wait_seconds", 60)),
        start_delay=float(o.get("start_delay_seconds", 3)),
    )

    cfg.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cfg.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    q = raw.get("queue") or {}
    cfg.queue_csv = _path(base, q.get("csv") or "queue.csv")
    cfg.queue_name = str(q.get("name") or "daily")

    names = set()
    for i, s in enumerate(raw.get("fixed_streams") or [], 1):
        where = f"fixed_streams[{i}]"
        for req in ("name", "time", "file", "title"):
            if not s.get(req):
                raise ConfigError(f"{where}: '{req}' is required")
        t = str(s["time"]).strip()
        if not _TIME_RE.match(t):
            raise ConfigError(f"{where}: time must be HH:MM, got '{t}'")
        h, m = (int(x) for x in t.split(":"))
        if h > 23 or m > 59:
            raise ConfigError(f"{where}: invalid time '{t}'")
        if s["name"] in names:
            raise ConfigError(f"{where}: duplicate name '{s['name']}'")
        names.add(s["name"])
        cfg.fixed.append(
            FixedStream(
                name=str(s["name"]),
                time=f"{h:02d}:{m:02d}",
                file=str(s["file"]),
                title=str(s["title"]),
                description=str(s.get("description") or ""),
                days=_days(s.get("days"), where),
            )
        )
    return cfg


def resolve_media(cfg: Config, file: str) -> Path:
    p = Path(file).expanduser()
    if p.is_absolute():
        return p
    return (cfg.media_dir or cfg.base_dir) / p
