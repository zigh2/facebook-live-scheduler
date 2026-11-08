import subprocess
import threading
from datetime import datetime
from pathlib import Path

import pytest

from fblive.config import Config, FacebookSettings, ObsSettings
from fblive.engine import EngineError
from fblive.engine_obs import ObsEngine
from fblive.facebook import FacebookError, FacebookLive, split_stream_url
from fblive.notify import Notifier
from fblive.obsws import ObsClient, ObsError
from fblive.probe import probe
from fblive.runner import run_stream
from fblive.schedule import PlannedStream


def no_sleep(_):
    import time; time.sleep(0.01)


def make(obs, graph, tmp_path, **cfg_kw):
    cfg = Config(base_dir=tmp_path, poll_interval=0.05, retry_delay=0.05, ffprobe="definitely-not-installed", **cfg_kw)
    cfg.obs = ObsSettings(port=obs.port, password="pw", start_delay=0.05)
    engine = ObsEngine(cfg.obs, sleep=no_sleep)
    fb = FacebookLive("PAGE", "TOKEN", "v26.0", graph.base)
    media = tmp_path / "v.mp4"
    media.write_bytes(b"x")
    p = PlannedStream("k", "morning", datetime.now(), "Title", "Desc", str(media), "fixed")
    return cfg, engine, fb, p


def test_split_stream_url():
    assert split_stream_url("rtmps://h:443/rtmp/FB-1-0-abc") == ("rtmps://h:443/rtmp/", "FB-1-0-abc")
    with pytest.raises(FacebookError):
        split_stream_url("rtmps://h:443/nothing")


def test_obs_auth_ok_and_wrong_password(obs):
    ObsClient("127.0.0.1", obs.port, "pw").connect()
    with pytest.raises(ObsError):
        ObsClient("127.0.0.1", obs.port, "wrong").connect()


def test_full_broadcast(obs, graph, tmp_path):
    cfg, engine, fb, p = make(obs, graph, tmp_path)
    out = run_stream(cfg, p, engine, fb, Notifier())
    assert out.status == "ok", out
    created = [c for c in graph.calls if c[1].endswith("/live_videos")][0][2]
    assert created["title"] == "Title" and created["status"] == "LIVE_NOW" and created["description"] == "Desc"
    assert obs.service["streamServiceSettings"] == {"server": "rtmps://live-api-s.facebook.com:443/rtmp/", "key": "FB-999-0-KEY", "use_auth": False}
    assert obs.media_file == p.file
    assert obs.streaming is False and obs.current == "FBLive_Idle"
    assert any(c[2].get("end_live_video") == "true" for c in graph.calls if c[0] == "POST")
    assert "FBLive" in obs.scenes and "FBLive_Idle" in obs.scenes and "FBLive_Media" in obs.inputs


def test_test_mode_is_unpublished(obs, graph, tmp_path):
    cfg, engine, fb, p = make(obs, graph, tmp_path)
    assert run_stream(cfg, p, engine, fb, Notifier(), test=True).status == "ok"
    assert [c for c in graph.calls if c[1].endswith("/live_videos")][0][2]["status"] == "UNPUBLISHED"


def test_retry_on_temporary_facebook_error(obs, graph, tmp_path):
    graph.fail_create = 1
    cfg, engine, fb, p = make(obs, graph, tmp_path)
    assert run_stream(cfg, p, engine, fb, Notifier()).status == "ok"
    assert len([c for c in graph.calls if c[1].endswith("/live_videos")]) == 2


def test_bad_token_fails_without_retry(obs, graph, tmp_path):
    cfg, engine, fb, p = make(obs, graph, tmp_path)
    fb.token = "WRONG"
    out = run_stream(cfg, p, engine, fb, Notifier())
    assert out.status == "failed" and "setup-token" in out.detail
    assert len([c for c in graph.calls if c[1].endswith("/live_videos")]) == 1
    assert obs.streaming is False


def test_missing_file(obs, graph, tmp_path):
    cfg, engine, fb, p = make(obs, graph, tmp_path)
    Path(p.file).unlink()
    assert run_stream(cfg, p, engine, fb, Notifier()).status == "failed"
    assert graph.calls == []


def test_refuses_to_hijack_manual_stream(obs, graph, tmp_path):
    obs.streaming = True
    cfg, engine, fb, p = make(obs, graph, tmp_path, retries=0)
    out = run_stream(cfg, p, engine, fb, Notifier())
    assert out.status == "failed" and "already streaming" in out.detail
    assert obs.streaming is True   # untouched


def test_dropped_stream_is_reported(obs, graph, tmp_path):
    obs.media_seconds = 30
    cfg, engine, fb, p = make(obs, graph, tmp_path)
    t = threading.Timer(0.6, lambda: setattr(obs, "streaming", False)); t.start()
    out = run_stream(cfg, p, engine, fb, Notifier())
    assert out.status == "interrupted"


def test_stop_event(obs, graph, tmp_path):
    obs.media_seconds = 30
    cfg, engine, fb, p = make(obs, graph, tmp_path)
    stop = threading.Event()
    threading.Timer(0.6, stop.set).start()
    assert run_stream(cfg, p, engine, fb, Notifier(), stop=stop).status == "stopped"
    assert obs.streaming is False


def test_probe_real_files(tmp_path):
    v = tmp_path / "av.mp4"; n = tmp_path / "video_only.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-f", "lavfi", "-i",
                    "sine=frequency=440", "-t", "2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(v)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-t", "1", "-c:v", "libx264", str(n)], check=True)
    i = probe(v)
    assert i.has_audio and i.has_video and 1.5 < i.duration < 2.6 and i.height == 240
    assert probe(n).has_audio is False


def test_runner_rejects_file_without_audio(obs, graph, tmp_path):
    n = tmp_path / "video_only.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-t", "1", "-c:v", "libx264", str(n)], check=True)
    cfg, engine, fb, p = make(obs, graph, tmp_path)
    cfg.ffprobe = "ffprobe"
    p = PlannedStream("k", "m", datetime.now(), "T", "", str(n), "fixed")
    out = run_stream(cfg, p, engine, fb, Notifier())
    assert out.status == "failed" and "no audio" in out.detail
