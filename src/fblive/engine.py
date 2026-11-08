"""Engine interface: something that can play a media file into an RTMP(S) destination."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


class EngineError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class EngineStatus:
    streaming: bool
    finished: bool
    detail: str = ""


class Engine(ABC):
    name = "engine"

    @abstractmethod
    def connect(self) -> None:
        """Make sure the engine is reachable (launching it if configured)."""

    @abstractmethod
    def prepare(self, media: Path) -> list:
        """Load the media; return a list of warnings."""

    @abstractmethod
    def start(self, server: str, key: str) -> None:
        """Start streaming to the destination and begin playing the media."""

    @abstractmethod
    def poll(self) -> EngineStatus:
        """Current state; called every few seconds while live."""

    @abstractmethod
    def stop(self) -> dict:
        """Stop streaming (idempotent). Returns optional statistics."""
