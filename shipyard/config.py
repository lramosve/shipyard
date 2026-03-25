from pathlib import Path

from pydantic_settings import BaseSettings

# Look for .env in CWD first, then in the shipyard package directory
_env_files = [".env"]
_package_env = Path(__file__).resolve().parent.parent / ".env"
if _package_env.is_file():
    _env_files.append(str(_package_env))


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    langsmith_api_key: str = ""
    langsmith_project: str = "shipyard"
    langsmith_tracing: str = "true"
    llm_provider: str = "anthropic"  # "anthropic" or "openai"
    llm_model: str = "claude-sonnet-4-20250514"
    working_directory: str = "."
    max_tool_output_chars: int = 50000
    context_window_size: int = 200000
    compaction_threshold: float = 0.85
    db_path: str = "shipyard.db"

    model_config = {"env_file": _env_files, "env_file_encoding": "utf-8"}


settings = Settings()
