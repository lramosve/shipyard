"""list_files tool — list directory contents."""

from __future__ import annotations

from pathlib import Path

from shipyard.tools.base import ToolResult


def list_files(
    directory: str = ".",
    pattern: str | None = None,
    recursive: bool = False,
) -> ToolResult:
    """List files in a directory, optionally filtered by glob pattern.

    Args:
        directory: Directory to list.
        pattern: Optional glob pattern (e.g., "*.py").
        recursive: If True, list recursively.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        return ToolResult(output=f"Error: Directory not found: {directory}", is_error=True)

    try:
        if pattern:
            items = list(root.rglob(pattern) if recursive else root.glob(pattern))
        else:
            items = list(root.rglob("*") if recursive else root.iterdir())

        # Filter out hidden and common skip dirs
        filtered: list[str] = []
        for item in sorted(items):
            rel = str(item.relative_to(root))
            if any(skip in rel for skip in (".git", "__pycache__", "node_modules", ".venv", "venv")):
                continue
            suffix = "/" if item.is_dir() else ""
            filtered.append(f"{rel}{suffix}")

        if not filtered:
            return ToolResult(output=f"No files found in {directory}" + (f" matching '{pattern}'" if pattern else ""))

        header = f"Contents of {directory}"
        if pattern:
            header += f" (pattern: {pattern})"
        header += f" — {len(filtered)} items:\n"
        return ToolResult(output=header + "\n".join(filtered))

    except Exception as e:
        return ToolResult(output=f"Error listing directory: {e}", is_error=True)
