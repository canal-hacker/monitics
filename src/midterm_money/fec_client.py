import json
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests
from requests import Response
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import settings


class FECClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.FEC_API_KEY
        self.base_url = (base_url or settings.FEC_BASE_URL).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _build_url(self, endpoint: str) -> str:
        if endpoint.startswith("http"):
            return endpoint
        return f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Response:
        params = params.copy() if params else {}
        params["api_key"] = self.api_key
        url = self._build_url(endpoint)

        response = self.session.get(url, params=params, timeout=30)
        if response.status_code == 429:
            raise requests.HTTPError("Rate limit exceeded", response=response)
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
            time.sleep(0.5)

    def fetch_all(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return list(self.paginate(endpoint, params=params))

    def save_raw_json(self, data: Any, file_path: Path) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
