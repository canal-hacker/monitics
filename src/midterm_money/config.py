import os
import re
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from a .env file if present.
env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=env_path)


@dataclass(frozen=True)
class Settings:
    FEC_API_KEY: str
    FEC_API_KEYS: tuple[str, ...]
    FEC_BASE_URL: str
    ELECTION_CYCLE: int
    OFFICE: str
    USE_DEMO_KEY: bool
    FEC_REQUEST_INTERVAL_SECONDS: float
    FEC_RATE_LIMIT_SLEEP_SECONDS: float
    FEC_REQUEST_TIMEOUT_SECONDS: float
    POLYMARKET_GAMMA_BASE_URL: str


def _load_fec_api_keys() -> tuple[str, ...]:
    raw_keys = [os.getenv("FEC_API_KEY", "DEMO_KEY").strip()]
    extra_key_names = sorted(
        (
            name
            for name in os.environ
            if re.fullmatch(r"FEC_API_KEY_\d+", name)
        ),
        key=lambda name: int(name.rsplit("_", 1)[1]),
    )

    for name in extra_key_names:
        value = os.getenv(name, "").strip()
        if value:
            raw_keys.append(value)

    unique_keys: list[str] = []
    for key in raw_keys:
        if key and key not in unique_keys:
            unique_keys.append(key)

    return tuple(unique_keys or ["DEMO_KEY"])


def _load_settings() -> Settings:
    api_keys = _load_fec_api_keys()
    api_key = api_keys[0]
    base_url = os.getenv("FEC_BASE_URL", "https://api.open.fec.gov/v1")
    election_cycle = int(os.getenv("ELECTION_CYCLE", "2026"))
    office = os.getenv("OFFICE", "S")
    use_demo_key = all(key.strip() == "DEMO_KEY" for key in api_keys)
    request_interval_seconds = float(os.getenv("FEC_REQUEST_INTERVAL_SECONDS", "0.75"))
    rate_limit_sleep_seconds = float(os.getenv("FEC_RATE_LIMIT_SLEEP_SECONDS", "30"))
    request_timeout_seconds = float(os.getenv("FEC_REQUEST_TIMEOUT_SECONDS", "120"))
    polymarket_gamma_base_url = os.getenv("POLYMARKET_GAMMA_BASE_URL", "https://gamma-api.polymarket.com")

    return Settings(
        FEC_API_KEY=api_key,
        FEC_API_KEYS=api_keys,
        FEC_BASE_URL=base_url.rstrip("/"),
        ELECTION_CYCLE=election_cycle,
        OFFICE=office,
        USE_DEMO_KEY=use_demo_key,
        FEC_REQUEST_INTERVAL_SECONDS=request_interval_seconds,
        FEC_RATE_LIMIT_SLEEP_SECONDS=rate_limit_sleep_seconds,
        FEC_REQUEST_TIMEOUT_SECONDS=request_timeout_seconds,
        POLYMARKET_GAMMA_BASE_URL=polymarket_gamma_base_url.rstrip("/"),
    )


settings = _load_settings()
