import json
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests
from requests import Response
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import settings


class FECClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        request_interval_seconds: Optional[float] = None,
        rate_limit_sleep_seconds: Optional[float] = None,
    ):
        self.api_key = api_key or settings.FEC_API_KEY
        self.base_url = (base_url or settings.FEC_BASE_URL).rstrip("/")
        self.request_interval_seconds = (
            settings.FEC_REQUEST_INTERVAL_SECONDS
            if request_interval_seconds is None
            else request_interval_seconds
        )
        self.rate_limit_sleep_seconds = (
            settings.FEC_RATE_LIMIT_SLEEP_SECONDS
            if rate_limit_sleep_seconds is None
            else rate_limit_sleep_seconds
        )
        self._last_request_monotonic: Optional[float] = None
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _build_url(self, endpoint: str) -> str:
        if endpoint.startswith("http"):
            return endpoint
        return f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    def _wait_for_request_slot(self) -> None:
        if self.request_interval_seconds <= 0 or self._last_request_monotonic is None:
            return

        elapsed = time.monotonic() - self._last_request_monotonic
        remaining = self.request_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _retry_after_seconds(self, response: Response) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after is None:
            return self.rate_limit_sleep_seconds

        try:
            return max(float(retry_after), 0.0)
        except ValueError:
            return self.rate_limit_sleep_seconds

    def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Response:
        params = params.copy() if params else {}
        params["api_key"] = self.api_key
        url = self._build_url(endpoint)

        self._wait_for_request_slot()
        response = self.session.get(url, params=params, timeout=30)
        self._last_request_monotonic = time.monotonic()
        if response.status_code == 429:
            retry_after = self._retry_after_seconds(response)
            if retry_after > 0:
                print(
                    f"[FEC] Rate limit hit for {endpoint}; sleeping {retry_after:.1f}s before retry.",
                    flush=True,
                )
                time.sleep(retry_after)
            raise requests.HTTPError(
                f"Rate limit exceeded for {endpoint}; retrying after {retry_after:.1f}s",
                response=response,
            )
        response.raise_for_status()
        return response

    @retry(
        retry=retry_if_exception_type((requests.RequestException, requests.HTTPError)),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self._request(endpoint, params=params)
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected response format from FEC API")
        return data

    def paginate(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        params = params.copy() if params else {}
        params.setdefault("per_page", 100)
        page = 1
        while True:
            params["page"] = page
            data = self.get(endpoint, params=params)
            results = data.get("results", [])
            if not isinstance(results, list):
                raise ValueError("Unexpected results format from FEC API pagination")
            for item in results:
                yield item
            pagination = data.get("pagination", {})
            if not pagination:
                break
            current_page = pagination.get("page", page)
            pages = pagination.get("pages")
            if pages is None or current_page >= pages:
                break
            page = current_page + 1

    def fetch_all(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return list(self.paginate(endpoint, params=params))

    def save_raw_json(self, data: Any, file_path: Path) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
