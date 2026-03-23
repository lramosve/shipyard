"""search_files tool — regex search across files."""

from __future__ import annotations

import re
from pathlib import Path

from shipyard.tools.base import ToolResult

MAX_RESULTS = 50


def search_files(
    pattern: str,
    directory: str = ".",
    file_glob: str = "*",
) -> ToolResult:
    """Search for a regex pattern across files in a directory.

    Args:
        pattern: Regex pattern to search for.
        directory: Root directory to search in.
        file_glob: Glob pattern to filter files (e.g., "*.py").
    """
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return ToolResult(output=f"Error: Invalid regex pattern: {e}", is_error=True)

    root = Path(directory).resolve()
    if not root.is_dir():
        return ToolResult(output=f"Error: Directory not found: {directory}", is_error=True)

    matches: list[str] = []
    files_searched = 0

    for file_path in root.rglob(file_glob):
        if not file_path.is_file():
            continue
        # Skip binary / hidden / venv
        rel = str(file_path.relative_to(root))
        if any(part.startswith(".") for part in file_path.parts):
            continue
        if any(skip in rel for skip in ("node_modules", "__pycache__", ".git", "venv", ".venv")):
            continue

        files_searched += 1
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        for i, line in enumerate(content.splitlines(), 1):
            if regex.search(line):
                matches.append(f"{file_path}:{i}: {line.rstrip()}")
                if len(matches) >= MAX_RESULTS:
                    break
        if len(matches) >= MAX_RESULTS:
            break

    if not matches:
        return ToolResult(output=f"No matches for '{pattern}' in {files_searched} files searched.")

    header = f"Found {len(matches)} match(es) in {files_searched} files searched"
    if len(matches) >= MAX_RESULTS:
        header += f" (showing first {MAX_RESULTS})"
    header += ":\n"

    return ToolResult(output=header + "\n".join(matches))
