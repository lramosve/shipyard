"""web_fetch tool — fetch a URL and return its contents."""

from __future__ import annotations

import html.parser
import io
import logging
import urllib.request
import urllib.error

from shipyard.config import settings
from shipyard.tools.base import ToolResult

logger = logging.getLogger(__name__)


class _HTMLTextExtractor(html.parser.HTMLParser):
    """Simple HTML to text converter using stdlib only."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header"}

    def __init__(self):
        super().__init__()
        self._result: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._result.append(text)

    def get_text(self) -> str:
        return "\n".join(self._result)


def _extract_text(html_content: str) -> str:
    """Strip HTML tags and return plain text."""
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(html_content)
        return extractor.get_text()
    except Exception:
        return html_content


def web_fetch(url: str, extract_text: bool = True) -> ToolResult:
    """Fetch a URL and return its contents.

    Args:
        url: The URL to fetch.
        extract_text: If True, strip HTML and return plain text. If False, return raw HTML.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Shipyard Agent) Python/3.12",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            content = resp.read().decode(charset, errors="replace")

        if extract_text and ("text/html" in (resp.headers.get("content-type", "") or "")):
            content = _extract_text(content)

        # Truncate
        max_chars = settings.max_tool_output_chars
        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n... (truncated)"

        return ToolResult(output=f"Fetched: {url}\n\n{content}")

    except urllib.error.HTTPError as e:
        return ToolResult(output=f"HTTP Error {e.code}: {url}", is_error=True)
    except urllib.error.URLError as e:
        return ToolResult(output=f"URL Error: {e.reason}", is_error=True)
    except Exception as e:
        logger.warning(f"Web fetch failed: {e}")
        return ToolResult(output=f"Fetch failed: {type(e).__name__}: {e}", is_error=True)
