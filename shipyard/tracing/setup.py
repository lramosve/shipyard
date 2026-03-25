import logging
import os
import warnings

from shipyard.config import settings


def configure_tracing() -> None:
    """Set LangSmith environment variables for automatic tracing.

    If no API key is available, tracing is disabled silently.
    """
    if settings.langsmith_api_key:
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
        os.environ.setdefault("LANGSMITH_TRACING", settings.langsmith_tracing)
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    else:
        os.environ["LANGSMITH_TRACING"] = "false"

    # Suppress noisy LangSmith warnings when tracing is off or key is missing
    logging.getLogger("langsmith").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", message=".*LangSmith.*")
    warnings.filterwarnings("ignore", message=".*API key.*")
