"""Minimal obs-websocket v5 client (OBS 28+ has the server built in)."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid

import websocket

log = logging.getLogger(__name__)


class ObsError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def make_auth(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class ObsClient:
    def __init__(self, host: str = "localhost", port: int = 4455, password: str = "", timeout: float = 10.0):
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self._ws = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def connect(self) -> None:
        self.close()
        url = f"ws://{self.host}:{self.port}"
        ws = None
        try:
            ws = websocket.create_connection(url, timeout=self.timeout)
            hello = json.loads(ws.recv())
            if hello.get("op") != 0:
                raise ObsError("unexpected handshake from OBS")
            ident: dict = {"rpcVersion": 1, "eventSubscriptions": 0}
            auth = hello["d"].get("authentication")
            if auth:
                if not self.password:
                    raise ObsError("OBS WebSocket requires a password (set OBS_PASSWORD in .env)")
                ident["authentication"] = make_auth(self.password, auth["salt"], auth["challenge"])
            ws.send(json.dumps({"op": 1, "d": ident}))
            reply = json.loads(ws.recv())
            if reply.get("op") != 2:
                raise ObsError("OBS rejected the identification")
        except ObsError:
            if ws:
                ws.close()
            raise
        except (OSError, websocket.WebSocketException, ValueError) as e:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass
            raise ObsError(f"cannot connect to OBS at {url} (wrong password / WebSocket disabled / OBS closed?): {e}") from None
        self._ws = ws

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def request(self, request_type: str, data: dict | None = None) -> dict:
        if self._ws is None:
            self.connect()
        rid = uuid.uuid4().hex
        msg: dict = {"op": 6, "d": {"requestType": request_type, "requestId": rid}}
        if data:
            msg["d"]["requestData"] = data
        try:
            self._ws.send(json.dumps(msg))
            while True:
                m = json.loads(self._ws.recv())
                if m.get("op") == 7 and m["d"].get("requestId") == rid:
                    break
        except (OSError, websocket.WebSocketException, ValueError) as e:
            self.close()
            raise ObsError(f"OBS connection lost during {request_type}: {e}") from None
        d = m["d"]
        st = d.get("requestStatus", {})
        if not st.get("result"):
            raise ObsError(f"OBS {request_type} failed: {st.get('comment') or st.get('code')}", code=st.get("code"))
        return d.get("responseData") or {}
