from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    project_id: str
    location: str = "US"
    max_bytes_billed: int = 100_000_000
    timeout_seconds: int = 30
    max_result_rows: int = 100
    business_timezone: str = "America/Los_Angeles"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    live_schema: bool = True


def load_settings() -> Settings:
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project_id:
        raise ValueError("Set GOOGLE_CLOUD_PROJECT in .env")
    live = os.getenv("LIVE_SCHEMA", "true").strip().lower() in {"1", "true", "yes"}
    return Settings(
        project_id=project_id,
        location=os.getenv("BIGQUERY_LOCATION", "US"),
        max_bytes_billed=int(os.getenv("MAX_BYTES_BILLED", "100000000")),
        timeout_seconds=int(os.getenv("QUERY_TIMEOUT_SECONDS", "30")),
        max_result_rows=int(os.getenv("MAX_RESULT_ROWS", "100")),
        business_timezone=os.getenv("BUSINESS_TIMEZONE", "America/Los_Angeles"),
        llm_base_url=os.getenv("LLM_BASE_URL") or None,
        llm_api_key=os.getenv("LLM_API_KEY") or None,
        llm_model=os.getenv("LLM_MODEL") or None,
        live_schema=live,
    )
