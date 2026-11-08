"""Planning: which streams are due, from fixed slots + the daily CSV queue."""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .config import Config, resolve_media

log = logging.getLogger(__name__)

WEEKDAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y")
_TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p")


def expand_templates(text: str, day: date) -> str:
    return text.replace("{date}", day.isoformat()).replace("{weekday_ar}", WEEKDAYS_AR[day.weekday()])


def parse_date(s: str) -> date:
    s = s.strip()
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    raise ValueError(f"bad date '{s}' (use YYYY-MM-DD)")


def parse_time(s: str) -> time:
    s = s.strip()
    for f in _TIME_FORMATS:
        try:
            return datetime.strptime(s, f).time().replace(second=0, microsecond=0)
        except ValueError:
            pass
    raise ValueError(f"bad time '{s}' (use HH:MM)")


@dataclass(frozen=True)
class QueueRow:
    day: date
    at: time
    title: str
    description: str
    file: str
    line: int


@dataclass(frozen=True)
class PlannedStream:
    key: str
    name: str
    start: datetime
    title: str
    description: str
    file: str
    source: str  # "fixed" | "queue"


def read_queue(path: Path | None) -> tuple[list[QueueRow], list[str]]:
    """Read the CSV queue. Returns (rows, errors). Never raises on bad rows."""
    rows: list[QueueRow] = []
    errors: list[str] = []
    if path is None or not Path(path).is_file():
        return rows, errors
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        return rows, [f"cannot read {path}: {e} (save the CSV as UTF-8)"]
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if not lines:
        return rows, errors
    header = lines[0]
    delim = max((",", ";", "\t"), key=header.count)
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delim)
    reader.fieldnames = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    missing = {"date", "time", "title", "file"} - set(reader.fieldnames)
    if missing:
        return rows, [f"queue CSV is missing columns: {', '.join(sorted(missing))}"]
    for n, rec in enumerate(reader, start=2):
        try:
            day = parse_date(rec.get("date") or "")
            at = parse_time(rec.get("time") or "")
            title = (rec.get("title") or "").strip()
            file = (rec.get("file") or "").strip()
            if not title or not file:
                raise ValueError("title and file are required")
            rows.append(QueueRow(day, at, title, (rec.get("description") or "").strip(), file, n))
        except ValueError as e:
            errors.append(f"queue line {n}: {e}")
    return rows, errors


def plan_for_day(cfg: Config, day: date, queue: list[QueueRow]) -> list[PlannedStream]:
    out: list[PlannedStream] = []
    for s in cfg.fixed:
        if day.weekday() not in s.days:
            continue
        start = datetime.combine(day, parse_time(s.time))
        out.append(
            PlannedStream(
                key=f"{day.isoformat()}|{s.time}|{s.name}",
                name=s.name,
                start=start,
                title=expand_templates(s.title, day),
                description=expand_templates(s.description, day),
                file=str(resolve_media(cfg, s.file)),
                source="fixed",
            )
        )
    for r in queue:
        if r.day != day:
            continue
        hhmm = r.at.strftime("%H:%M")
        out.append(
            PlannedStream(
                key=f"{day.isoformat()}|{hhmm}|{cfg.queue_name}",
                name=cfg.queue_name,
                start=datetime.combine(day, r.at),
                title=expand_templates(r.title, day),
                description=expand_templates(r.description, day),
                file=str(resolve_media(cfg, r.file)),
                source="queue",
            )
        )
    return sorted(out, key=lambda p: p.start)


def plan_range(cfg: Config, first_day: date, days: int, queue: list[QueueRow]) -> list[PlannedStream]:
    out: list[PlannedStream] = []
    for i in range(days):
        out.extend(plan_for_day(cfg, first_day + timedelta(days=i), queue))
    return out


def due_streams(planned: list[PlannedStream], now: datetime, grace: timedelta, done: set[str]) -> list[PlannedStream]:
    return sorted(
        (p for p in planned if p.key not in done and p.start <= now <= p.start + grace),
        key=lambda p: p.start,
    )


def expired_streams(planned: list[PlannedStream], now: datetime, grace: timedelta, done: set[str]) -> list[PlannedStream]:
    return [p for p in planned if p.key not in done and now > p.start + grace]
