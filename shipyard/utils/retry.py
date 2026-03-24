"""Retry with exponential backoff for transient LLM API failures."""

from __future__ import annotations

import functools
import logging
import time

logger = logging.getLogger(__name__)

TRANSIENT_ERROR_NAMES = {
    "RateLimitError",
    "APIStatusError",
    "InternalServerError",
    "APIConnectionError",
    "APITimeoutError",
    "ConnectionError",
    "TimeoutError",
}


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__
    if name in TRANSIENT_ERROR_NAMES:
        return True
    # Check for HTTP 5xx in APIStatusError
    if hasattr(exc, "status_code"):
        return exc.status_code >= 500 or exc.status_code == 429
    return False


def with_retry(max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
    """Decorator that retries on transient errors with exponential backoff."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries or not _is_transient(e):
                        raise
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(f"Retry {attempt + 1}/{max_retries} after {delay:.1f}s: {e}")
                    time.sleep(delay)

        return wrapper

    return decorator
