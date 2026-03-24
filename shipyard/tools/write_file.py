"""write_file tool — create new files or full overwrite."""

from __future__ import annotations

from pathlib import Path

from shipyard.tools.base import FileReadTracker, ToolResult
from shipyard.tools.snapshots import FileSnapshotStore


def write_file(
    file_path: str,
    content: str,
    *,
    tracker: FileReadTracker,
    snapshot_store: FileSnapshotStore | None = None,
) -> ToolResult:
    """Write content to a file. Creates parent directories if needed.

    For existing files, the file must have been read first (to prevent
    accidental overwrites of unknown content).
    """
    path = Path(file_path).resolve()

    # Guard: if file exists, must have been read first
    if path.is_file() and not tracker.was_read(str(path)):
        return ToolResult(
            output=f"Error: File '{file_path}' exists but has not been read in this session. "
            f"Read the file first to confirm you want to overwrite it.",
            is_error=True,
        )

    try:
        # Snapshot existing content before overwrite
        if snapshot_store and path.is_file():
            existing = path.read_text(encoding="utf-8")
            snapshot_store.save_snapshot(str(path), existing)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        tracker.record_read(str(path))
        lines = content.count("\n") + 1
        action = "Overwrote" if path.is_file() else "Created"
        return ToolResult(output=f"{action} {file_path} ({lines} lines).")
    except Exception as e:
        return ToolResult(output=f"Error writing file: {e}", is_error=True)
