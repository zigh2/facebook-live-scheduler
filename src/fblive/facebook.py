"""Facebook Live Video API (Graph API) client."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

_RETRYABLE_CODES = {1, 2, 4, 17, 32, 341}


class FacebookError(Exception):
    def __init__(self, message: str, code: int | None = None, subcode: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.retryable = retryable


@dataclass
class LiveVideo:
    id: str
    secure_stream_url: str
    stream_url: str = ""


def split_stream_url(url: str) -> tuple[str, str]:
    """'rtmps://host:443/rtmp/KEY' -> ('rtmps://host:443/rtmp/', 'KEY')."""
    marker = "/rtmp/"
    if marker not in url:
        raise FacebookError(f"unexpected stream url format: {url[:40]}...")
    server, key = url.split(marker, 1)
    if not key:
        raise FacebookError("stream url has no key")
    return server + marker, key


class FacebookLive:
    def __init__(self, page_id: str, token: str, graph_version: str = "v26.0",
                 base_url: str = "https://graph.facebook.com", timeout: float = 30.0):
        self.page_id = page_id
        self.token = token
        self.graph_version = graph_version
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _call(self, method: str, path: str, params: dict | None = None, data: dict | None = None) -> dict:
        url = f"{self.base_url}/{self.graph_version}/{path.lstrip('/')}"
        params = dict(params or {})
        data = dict(data or {})
        if method == "GET":
            params["access_token"] = self.token
        else:
            data["access_token"] = self.token
        try:
            r = self.session.request(method, url, params=params or None, data=data or None, timeout=self.timeout)
        except requests.RequestException as e:
            raise FacebookError(f"network error: {str(e).replace(self.token, '***')}", retryable=True) from None
        try:
            payload = r.json()
        except ValueError:
            payload = {}
        if r.status_code >= 400 or (isinstance(payload, dict) and "error" in payload):
            err = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = err.get("code")
            msg = err.get("message") or f"HTTP {r.status_code}"
            retryable = r.status_code >= 500 or code in _RETRYABLE_CODES
            hint = " (token expired/invalid - run: fblive setup-token)" if code == 190 else ""
            raise FacebookError(f"Facebook API error {code}: {msg}{hint}", code=code,
                                subcode=err.get("error_subcode"), retryable=retryable)
        return payload

    def whoami(self) -> dict:
        return self._call("GET", "me", params={"fields": "id,name"})

    def create_live(self, title: str, description: str = "", status: str = "LIVE_NOW") -> LiveVideo:
        data = {"status": status, "title": title}
        if description:
            data["description"] = description
        p = self._call("POST", f"{self.page_id}/live_videos", data=data)
        url = p.get("secure_stream_url") or p.get("stream_url")
        if not p.get("id") or not url:
            raise FacebookError("Facebook did not return a stream url")
        return LiveVideo(id=str(p["id"]), secure_stream_url=url, stream_url=p.get("stream_url", ""))

    def end_live(self, live_id: str) -> None:
        self._call("POST", live_id, data={"end_live_video": "true"})

    def get_live(self, live_id: str, fields: str = "status,permalink_url") -> dict:
        return self._call("GET", live_id, params={"fields": fields})
