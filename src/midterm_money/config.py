import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from a .env file if present.
env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=env_path)


@dataclass(frozen=True)
class Settings:
    FEC_API_KEY: str
    FEC_BASE_URL: str
    ELECTION_CYCLE: int
    OFFICE: str
    USE_DEMO_KEY: bool
    FEC_REQUEST_INTERVAL_SECONDS: float
    FEC_RATE_LIMIT_SLEEP_SECONDS: float


def _load_settings() -> Settings:
    api_key = os.getenv("FEC_API_KEY", "DEMO_KEY")
    base_url = os.getenv("FEC_BASE_URL", "https://api.open.fec.gov/v1")
    election_cycle = int(os.getenv("ELECTION_CYCLE", "2026"))
    office = os.getenv("OFFICE", "S")
    use_demo_key = api_key.strip() == "DEMO_KEY"
    request_interval_seconds = float(os.getenv("FEC_REQUEST_INTERVAL_SECONDS", "0.75"))
    rate_limit_sleep_seconds = float(os.getenv("FEC_RATE_LIMIT_SLEEP_SECONDS", "30"))

    return Settings(
        FEC_API_KEY=api_key,
        FEC_BASE_URL=base_url.rstrip("/"),
        ELECTION_CYCLE=election_cycle,
        OFFICE=office,
        USE_DEMO_KEY=use_demo_key,
        FEC_REQUEST_INTERVAL_SECONDS=request_interval_seconds,
        FEC_RATE_LIMIT_SLEEP_SECONDS=rate_limit_sleep_seconds,
    )


settings = _load_settings()
