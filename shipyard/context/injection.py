"""Context injection — format external context for injection into the system prompt."""

from __future__ import annotations


def format_injected_context(context_items: list[dict]) -> str:
    """Format a list of context items into a string for the system prompt.

    Each item has keys: type, source, content.
    """
    if not context_items:
        return ""

    parts: list[str] = []
    for item in context_items:
        ctx_type = item.get("type", "general")
        source = item.get("source", "unknown")
        content = item.get("content", "")
        parts.append(
            f'<injected_context type="{ctx_type}" source="{source}">\n'
            f"{content}\n"
            f"</injected_context>"
        )

    return "\n\n".join(parts)


def load_context_from_file(file_path: str, context_type: str = "file") -> dict:
    """Load a context item from a file on disk."""
    from pathlib import Path

    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Context file not found: {file_path}")

    content = path.read_text(encoding="utf-8")
    return {
        "type": context_type,
        "source": path.name,
        "content": content,
    }
