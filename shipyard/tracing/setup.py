import os

from shipyard.config import settings


def configure_tracing() -> None:
    """Set LangSmith environment variables for automatic tracing."""
    os.environ.setdefault("LANGSMITH_TRACING", settings.langsmith_tracing)
    if settings.langsmith_api_key:
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
