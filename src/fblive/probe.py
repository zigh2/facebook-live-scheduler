"""Pre-flight check of a media file with ffprobe (optional but recommended)."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ProbeError(Exception):
    pass


@dataclass
class MediaInfo:
    duration: float | None
    has_audio: bool
    has_video: bool
    vcodec: str = ""
    width: int = 0
    height: int = 0


def probe(path: Path, ffprobe: str = "ffprobe") -> MediaInfo | None:
    """Return MediaInfo, or None if ffprobe is not installed. Raises ProbeError for unreadable files."""
    exe = shutil.which(ffprobe) or (ffprobe if Path(ffprobe).is_file() else None)
    if not exe:
        return None
    cmd = [exe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height", "-of", "json", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProbeError(f"ffprobe failed: {e}") from e
    if r.returncode != 0:
        raise ProbeError(f"ffprobe cannot read the file: {r.stderr.strip()[:200]}")
    try:
        j = json.loads(r.stdout or "{}")
    except ValueError as e:
        raise ProbeError("ffprobe returned invalid output") from e
    streams = j.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    dur = None
    try:
        dur = float(j.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass
    return MediaInfo(
        duration=dur,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        has_video=video is not None,
        vcodec=(video or {}).get("codec_name", ""),
        width=int((video or {}).get("width") or 0),
        height=int((video or {}).get("height") or 0),
    )
