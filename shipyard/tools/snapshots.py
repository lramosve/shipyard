"""File snapshot store for rollback capability."""

from __future__ import annotations

import os

from shipyard.tools.base import ToolResult


class FileSnapshotStore:
    """Stores pre-edit file content for rollback.

    Kept in memory on the session, not serialized into graph state.
    """

    def __init__(self):
        self._snapshots: dict[str, list[str]] = {}  # normalized_path -> [content_v0, v1, ...]

    def save_snapshot(self, path: str, content: str) -> int:
        """Save a snapshot of file content. Returns the version number."""
        normalized = os.path.normpath(os.path.abspath(path))
        if normalized not in self._snapshots:
            self._snapshots[normalized] = []
        self._snapshots[normalized].append(content)
        return len(self._snapshots[normalized]) - 1

    def get_snapshot(self, path: str, version: int = -1) -> str | None:
        """Get a snapshot. version=-1 means the most recent one."""
        normalized = os.path.normpath(os.path.abspath(path))
        versions = self._snapshots.get(normalized)
        if not versions:
            return None
        try:
            return versions[version]
        except IndexError:
            return None

    def list_snapshots(self, path: str) -> list[int]:
        """Return available version numbers for a file."""
        normalized = os.path.normpath(os.path.abspath(path))
        versions = self._snapshots.get(normalized, [])
        return list(range(len(versions)))

    def get_all_paths(self) -> list[str]:
        """Return all file paths that have snapshots."""
        return list(self._snapshots.keys())
