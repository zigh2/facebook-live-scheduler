"""OBS engine: drives OBS Studio through obs-websocket v5."""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

from .config import ObsSettings
from .engine import Engine, EngineError, EngineStatus
from .obsws import ObsClient, ObsError

log = logging.getLogger(__name__)

PLAYING = "OBS_MEDIA_STATE_PLAYING"
ENDED = "OBS_MEDIA_STATE_ENDED"
MEDIA_ERROR = "OBS_MEDIA_STATE_ERROR"
ACTION_RESTART = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
ACTION_STOP = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"


class ObsEngine(Engine):
    name = "obs"

    def __init__(self, settings: ObsSettings, client: ObsClient | None = None, sleep=time.sleep):
        self.s = settings
        self.c = client or ObsClient(settings.host, settings.port, settings.password, settings.timeout)
        self.sleep = sleep
        self._seen_playing = False
        self._owns = False  # True only while WE control OBS; stop() never touches a manual session

    # -- connection ---------------------------------------------------------
    def connect(self) -> None:
        try:
            self.c.connect()
            return
        except ObsError as first:
            err = first
        if not self.s.executable:
            raise EngineError(str(err))
        log.warning("OBS not reachable; launching %s", self.s.executable)
        exe = Path(self.s.executable)
        if not exe.is_file():
            raise EngineError(f"obs.executable not found: {exe}", retryable=False)
        kwargs = {"cwd": str(exe.parent)}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
        subprocess.Popen([str(exe), "--minimize-to-tray", "--disable-shutdown-check",
                          "--disable-missing-files-check"], **kwargs)
        deadline = time.monotonic() + self.s.launch_wait
        while time.monotonic() < deadline:
            self.sleep(2)
            try:
                self.c.connect()
                self.sleep(3)  # let OBS finish loading its scenes
                return
            except ObsError as e:
                err = e
        raise EngineError(f"OBS did not become reachable in {self.s.launch_wait:.0f}s: {err}")

    def _req(self, rtype: str, data: dict | None = None) -> dict:
        try:
            return self.c.request(rtype, data)
        except ObsError as e:
            raise EngineError(str(e)) from None

    # -- setup --------------------------------------------------------------
    def ensure_setup(self) -> list:
        """Create the live scene, the idle scene and the media source if missing; fit media to canvas."""
        warnings: list = []
        scenes = {s["sceneName"] for s in self._req("GetSceneList").get("scenes", [])}
        for name in (self.s.scene, self.s.idle_scene):
            if name not in scenes:
                log.info("creating OBS scene '%s'", name)
                self._req("CreateScene", {"sceneName": name})
        inputs = {i["inputName"] for i in self._req("GetInputList").get("inputs", [])}
        if self.s.input_name not in inputs:
            log.info("creating OBS media source '%s'", self.s.input_name)
            self._req("CreateInput", {
                "sceneName": self.s.scene, "inputName": self.s.input_name, "inputKind": "ffmpeg_source",
                "inputSettings": self._media_settings(""), "sceneItemEnabled": True})
        try:
            item_id = self._req("GetSceneItemId", {"sceneName": self.s.scene, "sourceName": self.s.input_name})["sceneItemId"]
        except EngineError:
            item_id = self._req("CreateSceneItem", {"sceneName": self.s.scene, "sourceName": self.s.input_name,
                                                    "sceneItemEnabled": True})["sceneItemId"]
        v = self._req("GetVideoSettings")
        bw, bh = int(v.get("baseWidth", 1920)), int(v.get("baseHeight", 1080))
        self._req("SetSceneItemTransform", {"sceneName": self.s.scene, "sceneItemId": item_id, "sceneItemTransform": {
            "positionX": bw / 2, "positionY": bh / 2, "alignment": 0, "boundsType": "OBS_BOUNDS_SCALE_INNER",
            "boundsAlignment": 0, "boundsWidth": bw, "boundsHeight": bh}})
        ow, oh, fps = int(v.get("outputWidth", bw)), int(v.get("outputHeight", bh)), v.get("fpsNumerator", 30) / max(1, v.get("fpsDenominator", 1))
        if oh > 1080 or ow > 1920:
            warnings.append(f"OBS output is {ow}x{oh}; set Settings > Video > Output resolution to 1920x1080 or lower for Facebook")
        if fps > 60:
            warnings.append(f"OBS runs at {fps:.0f} fps; use 30 (or 60 at most) for Facebook")
        return warnings

    def _media_settings(self, path: str) -> dict:
        return {"local_file": path, "is_local_file": True, "looping": False, "hw_decode": self.s.hw_decode,
                "close_when_inactive": True, "restart_on_activate": True, "clear_on_media_end": False}

    # -- engine API ---------------------------------------------------------
    def prepare(self, media: Path) -> list:
        warnings = self.ensure_setup()
        if self._req("GetStreamStatus").get("outputActive"):
            raise EngineError("OBS is already streaming (manual stream?) - not touching it")
        self._owns = True
        self._seen_playing = False
        self._req("SetCurrentProgramScene", {"sceneName": self.s.idle_scene})
        self._req("SetInputSettings", {"inputName": self.s.input_name, "overlay": True,
                                       "inputSettings": self._media_settings(str(media))})
        return warnings

    def _wait(self, predicate, timeout: float, what: str) -> None:
        deadline = time.monotonic() + timeout
        while True:
            if predicate():
                return
            if time.monotonic() > deadline:
                raise EngineError(f"timeout waiting for {what}")
            self.sleep(0.5)

    def start(self, server: str, key: str) -> None:
        self._req("SetStreamServiceSettings", {"streamServiceType": "rtmp_custom", "streamServiceSettings": {
            "server": server, "key": key, "use_auth": False}})
        self._req("StartStream")
        self._wait(lambda: self._req("GetStreamStatus").get("outputActive"), 30, "OBS to start streaming")
        self.sleep(self.s.start_delay)  # let Facebook ingest settle on the idle scene
        self._req("SetCurrentProgramScene", {"sceneName": self.s.scene})
        self._req("TriggerMediaInputAction", {"inputName": self.s.input_name, "mediaAction": ACTION_RESTART})

        def playing() -> bool:
            st = self._req("GetMediaInputStatus", {"inputName": self.s.input_name}).get("mediaState")
            if st == MEDIA_ERROR:
                raise EngineError("OBS could not play the media file (codec/hardware decoding problem?)", retryable=False)
            return st == PLAYING
        self._wait(playing, 30, "the media to start playing")
        self._seen_playing = True

    def poll(self) -> EngineStatus:
        ss = self._req("GetStreamStatus")
        streaming = bool(ss.get("outputActive"))
        m = self._req("GetMediaInputStatus", {"inputName": self.s.input_name})
        state = m.get("mediaState")
        if state == PLAYING:
            self._seen_playing = True
        if state == MEDIA_ERROR:
            raise EngineError("OBS media source reported an error", retryable=False)
        finished = self._seen_playing and state == ENDED
        cur, dur = m.get("mediaCursor"), m.get("mediaDuration")
        return EngineStatus(streaming=streaming, finished=finished, detail=f"{state} {cur}/{dur} ms")

    def stop(self) -> dict:
        stats: dict = {}
        if not self._owns:
            return stats
        try:
            ss = self.c.request("GetStreamStatus")
            stats = {"frames": ss.get("outputTotalFrames"), "skipped": ss.get("outputSkippedFrames")}
            if ss.get("outputActive"):
                self.c.request("StopStream")
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and self.c.request("GetStreamStatus").get("outputActive"):
                    self.sleep(0.5)
            self.c.request("SetCurrentProgramScene", {"sceneName": self.s.idle_scene})
            self.c.request("TriggerMediaInputAction", {"inputName": self.s.input_name, "mediaAction": ACTION_STOP})
        except ObsError as e:
            log.warning("error while stopping OBS stream: %s", e)
        self._owns = False
        return stats
