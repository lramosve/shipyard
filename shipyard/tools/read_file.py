"""read_file tool — reads file contents with optional line range."""

from __future__ import annotations

import os
from pathlib import Path

from shipyard.config import settings
from shipyard.tools.base import FileReadTracker, ToolResult


def read_file(
    file_path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    *,
    tracker: FileReadTracker,
) -> ToolResult:
    """Read a file's contents. Optionally specify a line range (1-indexed)."""
    try:
        path = Path(file_path).resolve()
        if not path.is_file():
            return ToolResult(output=f"Error: File not found: {file_path}", is_error=True)

        content = path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        if start_line is not None or end_line is not None:
            s = max((start_line or 1) - 1, 0)
            e = min(end_line or total_lines, total_lines)
            selected = lines[s:e]
            numbered = "".join(f"{s + i + 1:>6}\t{line}" for i, line in enumerate(selected))
            header = f"File: {file_path} (lines {s + 1}-{e} of {total_lines})\n"
        else:
            numbered = "".join(f"{i + 1:>6}\t{line}" for i, line in enumerate(lines))
            header = f"File: {file_path} ({total_lines} lines)\n"

        output = header + numbered

        # Truncate if too long
        if len(output) > settings.max_tool_output_chars:
            output = output[: settings.max_tool_output_chars] + "\n... (truncated)"

        # Record the read
        tracker.record_read(str(path))

        return ToolResult(output=output)

    except UnicodeDecodeError:
        return ToolResult(output=f"Error: File is not valid UTF-8: {file_path}", is_error=True)
    except Exception as e:
        return ToolResult(output=f"Error reading file: {e}", is_error=True)
