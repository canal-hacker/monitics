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
        request_timeout_seconds: Optional[float] = None,
    ):
        if api_key is None:
            self.api_keys = list(settings.FEC_API_KEYS)
        else:
            self.api_keys = [api_key]
        self._active_api_key_index = 0
        self.api_key = self.api_keys[self._active_api_key_index]
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
        self.request_timeout_seconds = (
            settings.FEC_REQUEST_TIMEOUT_SECONDS
            if request_timeout_seconds is None
            else request_timeout_seconds
        )
        self._last_request_monotonic: Optional[float] = None
        self._api_key_available_at: List[float] = [0.0] * len(self.api_keys)
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if len(self.api_keys) > 1:
            print(
                f"[FEC] Loaded {len(self.api_keys)} API key slots for rate-limit failover.",
                flush=True,
            )

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

    def _api_key_label(self, index: int) -> str:
        return f"{index + 1}/{len(self.api_keys)}"

    def _switch_api_key(self, index: int, reason: str) -> None:
        previous_index = self._active_api_key_index
        self._active_api_key_index = index
        self.api_key = self.api_keys[index]
        if index != previous_index and len(self.api_keys) > 1:
            print(
                f"[FEC] Switching API key to slot {self._api_key_label(index)} ({reason}).",
                flush=True,
            )

    def _available_api_key_indexes(self) -> List[int]:
        now = time.monotonic()
        return [
            index
            for index, available_at in enumerate(self._api_key_available_at)
            if available_at <= now
        ]

    def _next_available_api_key_index(self, exclude_current: bool = False) -> Optional[int]:
        available_indexes = self._available_api_key_indexes()
        if exclude_current:
            available_indexes = [
                index for index in available_indexes if index != self._active_api_key_index
            ]
        if not available_indexes:
            return None
        if self._active_api_key_index in available_indexes and not exclude_current:
            return self._active_api_key_index
        return available_indexes[0]

    def _wait_for_available_api_key(self) -> None:
        next_available_index = self._next_available_api_key_index()
        if next_available_index is not None:
            self._switch_api_key(next_available_index, "available")
            return

        next_available_at = min(self._api_key_available_at)
        sleep_for = max(next_available_at - time.monotonic(), 0.0)
        if sleep_for > 0:
            print(
                f"[FEC] All API keys cooling down; sleeping {sleep_for:.1f}s.",
                flush=True,
            )
            time.sleep(sleep_for)

        next_available_index = self._next_available_api_key_index()
        if next_available_index is not None:
            self._switch_api_key(next_available_index, "cooldown expired")

    def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Response:
        url = self._build_url(endpoint)

        while True:
            request_params = params.copy() if params else {}
            self._wait_for_available_api_key()
            request_params["api_key"] = self.api_key

            self._wait_for_request_slot()
            response = self.session.get(url, params=request_params, timeout=self.request_timeout_seconds)
            self._last_request_monotonic = time.monotonic()
            if response.status_code == 429:
                retry_after = self._retry_after_seconds(response)
                current_index = self._active_api_key_index
                self._api_key_available_at[current_index] = time.monotonic() + retry_after

                if len(self.api_keys) == 1:
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

                print(
                    f"[FEC] Rate limit hit for {endpoint} on API key slot "
                    f"{self._api_key_label(current_index)}; attempting failover.",
                    flush=True,
                )
                next_index = self._next_available_api_key_index(exclude_current=True)
                if next_index is not None:
                    self._switch_api_key(next_index, "rate limit failover")
                continue

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
