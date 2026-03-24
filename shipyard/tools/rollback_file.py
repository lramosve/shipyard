"""rollback_file tool — restore a file to a previous snapshot."""

from __future__ import annotations

from pathlib import Path

from shipyard.tools.base import FileReadTracker, ToolResult
from shipyard.tools.snapshots import FileSnapshotStore


def rollback_file(
    file_path: str,
    version: int = -1,
    *,
    snapshot_store: FileSnapshotStore,
    tracker: FileReadTracker,
) -> ToolResult:
    """Restore a file to a previous snapshot version.

    Args:
        file_path: Path to the file to rollback.
        version: Snapshot version to restore (-1 for most recent pre-edit state).
    """
    content = snapshot_store.get_snapshot(file_path, version)
    if content is None:
        available = snapshot_store.list_snapshots(file_path)
        if not available:
            return ToolResult(
                output=f"Error: No snapshots available for '{file_path}'.",
                is_error=True,
            )
        return ToolResult(
            output=f"Error: Version {version} not found. Available: {available}",
            is_error=True,
        )

    path = Path(file_path).resolve()
    try:
        path.write_text(content, encoding="utf-8")
        tracker.record_read(str(path))
        lines = content.count("\n") + 1
        return ToolResult(
            output=f"Rolled back {file_path} to version {version} ({lines} lines)."
        )
    except Exception as e:
        return ToolResult(output=f"Error rolling back file: {e}", is_error=True)
