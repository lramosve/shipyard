"""Tool foundation: result model and file read tracking."""

from __future__ import annotations

import os
import time

from pydantic import BaseModel


class ToolResult(BaseModel):
    output: str
    is_error: bool = False


class FileReadTracker:
    """Tracks which files have been read and when, for edit validation.

    Stored in AgentState and shared across all tool invocations in a session.
    """

    def __init__(self, reads: dict[str, float] | None = None):
        self._reads: dict[str, float] = reads or {}

    def record_read(self, path: str) -> None:
        normalized = os.path.normpath(os.path.abspath(path))
        # Use the file's own mtime to avoid clock skew between time.time() and os.path.getmtime()
        try:
            self._reads[normalized] = os.path.getmtime(normalized)
        except OSError:
            self._reads[normalized] = time.time()

    def was_read(self, path: str) -> bool:
        normalized = os.path.normpath(os.path.abspath(path))
        return normalized in self._reads

    def is_stale(self, path: str) -> bool:
        """True if the file was modified on disk after we last read it."""
        normalized = os.path.normpath(os.path.abspath(path))
        if normalized not in self._reads:
            return True
        try:
            mtime = os.path.getmtime(normalized)
            return mtime > self._reads[normalized]
        except OSError:
            return True

    def to_dict(self) -> dict[str, float]:
        return dict(self._reads)

    @classmethod
    def from_dict(cls, data: dict[str, float]) -> FileReadTracker:
        return cls(reads=data)
