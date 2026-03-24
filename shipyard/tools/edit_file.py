"""edit_file tool — anchor-based surgical replacement (old_string -> new_string).

This is the most critical tool in the agent. It implements:
- Uniqueness enforcement: old_string must match exactly once
- Read-before-write guard: file must have been read in this session
- Stale-read detection: file must not have been modified since last read
- Fuzzy match hint: if exact match fails, try whitespace-normalized search
"""

from __future__ import annotations

import os
from pathlib import Path

from shipyard.tools.base import FileReadTracker, ToolResult
from shipyard.tools.snapshots import FileSnapshotStore


def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    *,
    tracker: FileReadTracker,
    snapshot_store: FileSnapshotStore | None = None,
) -> ToolResult:
    """Make a surgical edit: replace old_string with new_string in file_path.

    old_string must appear exactly once in the file. The file must have been
    read via read_file in this session before editing.
    """
    path = Path(file_path).resolve()

    # Guard: file must exist
    if not path.is_file():
        return ToolResult(output=f"Error: File not found: {file_path}", is_error=True)

    # Guard: must have been read first
    if not tracker.was_read(str(path)):
        return ToolResult(
            output=f"Error: File has not been read in this session. Use read_file on '{file_path}' before editing.",
            is_error=True,
        )

    # Guard: stale read check
    if tracker.is_stale(str(path)):
        return ToolResult(
            output=f"Error: File '{file_path}' has been modified since last read. Please re-read the file before editing.",
            is_error=True,
        )

    # Guard: old_string and new_string must differ
    if old_string == new_string:
        return ToolResult(output="Error: old_string and new_string are identical. No change needed.", is_error=True)

    # Guard: old_string must not be empty
    if not old_string:
        return ToolResult(output="Error: old_string must not be empty. Use write_file to create new files.", is_error=True)

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return ToolResult(output=f"Error reading file: {e}", is_error=True)

    # Count occurrences
    count = content.count(old_string)

    if count == 0:
        return _handle_no_match(content, old_string, file_path)

    if count > 1:
        return _handle_multiple_matches(content, old_string, count)

    # Exactly one match — snapshot before editing
    if snapshot_store:
        snapshot_store.save_snapshot(str(path), content)

    new_content = content.replace(old_string, new_string, 1)

    try:
        path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return ToolResult(output=f"Error writing file: {e}", is_error=True)

    # Update the read tracker with the new write time
    tracker.record_read(str(path))

    # Summary
    old_lines = old_string.count("\n") + 1
    new_lines = new_string.count("\n") + 1
    total_lines = new_content.count("\n") + 1
    return ToolResult(
        output=f"Successfully edited {file_path}. Replaced {old_lines} lines with {new_lines} lines. File now has {total_lines} lines."
    )


def _handle_no_match(content: str, old_string: str, file_path: str) -> ToolResult:
    """Handle the case where old_string was not found. Try fuzzy hint."""
    # Try whitespace-normalized search
    normalized_old = _normalize_whitespace(old_string)
    lines = content.splitlines()
    normalized_lines = [_normalize_whitespace(line) for line in lines]

    # Search for the first line of old_string in the file
    old_first_line = normalized_old.splitlines()[0] if normalized_old.strip() else ""

    hint_lines: list[str] = []
    for i, norm_line in enumerate(normalized_lines):
        if old_first_line and old_first_line in norm_line:
            # Show context around the match
            start = max(0, i - 2)
            end = min(len(lines), i + 5)
            hint_lines.append(f"  Possible match near line {i + 1}:")
            for j in range(start, end):
                hint_lines.append(f"    {j + 1:>6}\t{lines[j]}")
            break

    msg = f"Error: old_string not found in '{file_path}'."
    if hint_lines:
        msg += "\n\nA whitespace-normalized match was found:\n" + "\n".join(hint_lines)
        msg += "\n\nPlease provide the exact text from the file, preserving whitespace and indentation."
    else:
        # Show a snippet of the file for orientation
        snippet_lines = lines[:20]
        snippet = "\n".join(f"    {i + 1:>6}\t{line}" for i, line in enumerate(snippet_lines))
        msg += f"\n\nFile begins with:\n{snippet}"
        if len(lines) > 20:
            msg += f"\n    ... ({len(lines)} total lines)"

    return ToolResult(output=msg, is_error=True)


def _handle_multiple_matches(content: str, old_string: str, count: int) -> ToolResult:
    """Handle multiple matches — return locations to help disambiguate."""
    lines = content.splitlines()
    match_locations: list[int] = []
    search_start = 0
    for _ in range(count):
        idx = content.index(old_string, search_start)
        line_num = content[:idx].count("\n") + 1
        match_locations.append(line_num)
        search_start = idx + 1

    locations = ", ".join(str(ln) for ln in match_locations)
    return ToolResult(
        output=f"Error: Found {count} matches for old_string at lines {locations}. "
        f"Include more surrounding context to make the match unique.",
        is_error=True,
    )


def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace for fuzzy comparison."""
    return "\n".join(line.strip() for line in text.splitlines())
