"""web_search tool — search the web for documentation, APIs, and solutions."""

from __future__ import annotations

import logging

from shipyard.tools.base import ToolResult

logger = logging.getLogger(__name__)


def web_search(query: str, max_results: int = 5) -> ToolResult:
    """Search the web and return titles, URLs, and snippets.

    Args:
        query: Search query string.
        max_results: Maximum number of results to return.
    """
    try:
        from duckduckgo_search import DDGS

        results = DDGS().text(query, max_results=max_results)

        if not results:
            return ToolResult(output=f"No results found for: {query}")

        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "No title")
            url = r.get("href", r.get("link", ""))
            snippet = r.get("body", r.get("snippet", ""))
            lines.append(f"{i}. {title}")
            lines.append(f"   URL: {url}")
            if snippet:
                lines.append(f"   {snippet}")
            lines.append("")

        return ToolResult(output="\n".join(lines))

    except ImportError:
        return ToolResult(
            output="Error: duckduckgo-search not installed. Run: pip install duckduckgo-search",
            is_error=True,
        )
    except Exception as e:
        logger.warning(f"Web search failed: {e}")
        return ToolResult(output=f"Search failed: {type(e).__name__}: {e}", is_error=True)
