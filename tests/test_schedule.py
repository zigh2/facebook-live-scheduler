from datetime import date, datetime, timedelta
from pathlib import Path

from fblive.config import Config, FixedStream, load_config
from fblive.schedule import (QueueRow, due_streams, expand_templates, expired_streams, parse_date, parse_time,
                             plan_for_day, read_queue)


def make_cfg(tmp_path):
    return Config(base_dir=tmp_path, media_dir=tmp_path / "media", fixed=[
        FixedStream("morning", "07:00", "a.mp4", "Morning {date}", "d1"),
        FixedStream("evening", "20:00", "b.mp4", "Evening", "d2", days=(0, 1)),
    ])


def test_parsers():
    assert parse_date("2026-11-09") == date(2026, 11, 9)
    assert parse_date("09/11/2026") == date(2026, 11, 9)
    assert parse_time("7:05").strftime("%H:%M") == "07:05"
    assert parse_time("8:30 PM").strftime("%H:%M") == "20:30"


def test_templates():
    assert expand_templates("{date} {weekday_ar}", date(2026, 11, 9)) == "2026-11-09 الاثنين"


def test_plan_fixed_days_and_paths(tmp_path):
    cfg = make_cfg(tmp_path)
    mon = date(2026, 11, 9)   # Monday
    wed = date(2026, 11, 11)
    assert [p.name for p in plan_for_day(cfg, mon, [])] == ["morning", "evening"]
    assert [p.name for p in plan_for_day(cfg, wed, [])] == ["morning"]
    p = plan_for_day(cfg, mon, [])[0]
    assert p.title == "Morning 2026-11-09"
    assert Path(p.file) == tmp_path / "media" / "a.mp4"


def test_queue_csv_semicolon_excel_bom(tmp_path):
    f = tmp_path / "q.csv"
    f.write_text("\ufeffdate;time;title;description;file\n2026-11-09;19:30;عنوان;وصف, مع فاصلة;x.mp4\nbad;1;;;\n", encoding="utf-8")
    rows, errors = read_queue(f)
    assert len(rows) == 1 and rows[0].title == "عنوان" and rows[0].description == "وصف, مع فاصلة"
    assert len(errors) == 1


def test_queue_missing_columns(tmp_path):
    f = tmp_path / "q.csv"
    f.write_text("date,time\n2026-11-09,19:30\n", encoding="utf-8")
    rows, errors = read_queue(f)
    assert rows == [] and "missing columns" in errors[0]


def test_queue_merged_into_plan(tmp_path):
    cfg = make_cfg(tmp_path)
    day = date(2026, 11, 9)
    q = [QueueRow(day, parse_time("22:15"), "Special", "desc", "c.mp4", 2)]
    names = [p.name for p in plan_for_day(cfg, day, q)]
    assert names == ["morning", "evening", "daily"]


def test_due_and_expired(tmp_path):
    cfg = make_cfg(tmp_path)
    day = date(2026, 11, 9)
    planned = plan_for_day(cfg, day, [])
    grace = timedelta(minutes=10)
    now = datetime(2026, 11, 9, 7, 3)
    assert [p.name for p in due_streams(planned, now, grace, set())] == ["morning"]
    assert due_streams(planned, now, grace, {planned[0].key}) == []
    assert due_streams(planned, datetime(2026, 11, 9, 6, 59), grace, set()) == []
    late = datetime(2026, 11, 9, 7, 11)
    assert due_streams(planned, late, grace, set()) == []
    assert [p.name for p in expired_streams(planned, late, grace, set())] == ["morning"]


def test_load_config(tmp_path, monkeypatch):
    monkeypatch.delenv("FB_PAGE_TOKEN", raising=False)
    (tmp_path / ".env").write_text("FB_PAGE_TOKEN=abc\nFB_PAGE_ID=77\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "fixed_streams:\n  - {name: m, time: '7:00', file: a.mp4, title: T, days: [mon, fri]}\n", encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.facebook.token == "abc" and cfg.facebook.page_id == "77"
    assert cfg.fixed[0].time == "07:00" and cfg.fixed[0].days == (0, 4)
