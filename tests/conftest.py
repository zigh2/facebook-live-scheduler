import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from websockets.sync.server import serve

from fblive.obsws import make_auth


class MockObs:
    """Just enough of obs-websocket v5 to exercise the engine."""

    def __init__(self, password="pw", media_seconds=1.0):
        self.password = password
        self.media_seconds = media_seconds
        self.scenes = ["Scene"]
        self.inputs = {}
        self.items = {}
        self.current = "Scene"
        self.streaming = False
        self.service = None
        self.media_started = None
        self.media_file = ""
        self.calls = []
        self.skip_playing = False
        self.connections = []

    def state(self):
        if self.current == "FBLive" and self.media_started and not self.skip_playing:
            return "OBS_MEDIA_STATE_PLAYING" if time.monotonic() - self.media_started < self.media_seconds else "OBS_MEDIA_STATE_ENDED"
        return "OBS_MEDIA_STATE_STOPPED"

    def handle(self, t, d):
        self.calls.append((t, d))
        if t == "GetSceneList":
            return {"scenes": [{"sceneName": n} for n in self.scenes]}
        if t == "CreateScene":
            self.scenes.append(d["sceneName"]); return {}
        if t == "GetInputList":
            return {"inputs": [{"inputName": n} for n in self.inputs]}
        if t == "CreateInput":
            self.inputs[d["inputName"]] = d["inputSettings"]; self.items[(d["sceneName"], d["inputName"])] = 1; return {"sceneItemId": 1}
        if t == "GetSceneItemId":
            k = (d["sceneName"], d["sourceName"])
            if k not in self.items:
                raise KeyError(600)
            return {"sceneItemId": self.items[k]}
        if t == "GetVideoSettings":
            return {"baseWidth": 1920, "baseHeight": 1080, "outputWidth": 1920, "outputHeight": 1080, "fpsNumerator": 30, "fpsDenominator": 1}
        if t == "SetSceneItemTransform":
            return {}
        if t == "GetStreamStatus":
            return {"outputActive": self.streaming, "outputTotalFrames": 100, "outputSkippedFrames": 0}
        if t == "SetCurrentProgramScene":
            self.current = d["sceneName"]
            if self.current == "FBLive":
                self.media_started = time.monotonic()
            return {}
        if t == "SetInputSettings":
            self.inputs[d["inputName"]].update(d["inputSettings"]); self.media_file = d["inputSettings"]["local_file"]; return {}
        if t == "SetStreamServiceSettings":
            self.service = d; return {}
        if t == "StartStream":
            self.streaming = True; return {}
        if t == "StopStream":
            self.streaming = False; return {}
        if t == "TriggerMediaInputAction":
            if d["mediaAction"].endswith("RESTART"):
                self.media_started = time.monotonic()
            return {}
        if t == "GetMediaInputStatus":
            return {"mediaState": self.state(), "mediaCursor": 0, "mediaDuration": int(self.media_seconds * 1000)}
        raise ValueError(f"unmocked request {t}")


def _obs_handler(mock):
    def handler(ws):
        mock.connections.append(ws)
        salt, challenge = "salt123", "chal456"
        ws.send(json.dumps({"op": 0, "d": {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1,
                                           "authentication": {"salt": salt, "challenge": challenge}}}))
        ident = json.loads(ws.recv())
        if ident["d"].get("authentication") != make_auth(mock.password, salt, challenge):
            ws.close(4009, "auth failed"); return
        ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        for raw in ws:
            m = json.loads(raw)["d"]
            try:
                data = mock.handle(m["requestType"], m.get("requestData") or {})
                status = {"result": True, "code": 100}
            except KeyError:
                data, status = {}, {"result": False, "code": 600, "comment": "not found"}
            ws.send(json.dumps({"op": 7, "d": {"requestType": m["requestType"], "requestId": m["requestId"],
                                               "requestStatus": status, "responseData": data}}))
    return handler


@pytest.fixture
def obs():
    mock = MockObs()
    server = serve(_obs_handler(mock), "127.0.0.1", 0, close_timeout=0.2)
    mock.port = server.socket.getsockname()[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield mock
    for c in list(mock.connections):
        try:
            c.close()
        except Exception:
            pass
    server.shutdown()


class MockGraph:
    def __init__(self):
        self.calls = []
        self.fail_create = 0


@pytest.fixture
def graph():
    state = MockGraph()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            from urllib.parse import parse_qs
            data = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
            state.calls.append(("POST", self.path, data))
            if data.get("access_token") != "TOKEN":
                return self._send(400, {"error": {"message": "bad token", "code": 190}})
            if self.path.endswith("/live_videos"):
                if state.fail_create > 0:
                    state.fail_create -= 1
                    return self._send(500, {"error": {"message": "temporary", "code": 2}})
                return self._send(200, {"id": "999", "secure_stream_url": "rtmps://live-api-s.facebook.com:443/rtmp/FB-999-0-KEY",
                                        "stream_url": "rtmp://x/rtmp/FB-999-0-KEY"})
            return self._send(200, {"success": True})
        def do_GET(self):
            state.calls.append(("GET", self.path, {}))
            self._send(200, {"id": "999", "permalink_url": "/page/videos/999", "status": "LIVE"})

    srv = HTTPServer(("127.0.0.1", 0), H)
    state.base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield state
    srv.shutdown()
