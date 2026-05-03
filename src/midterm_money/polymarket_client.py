from typing import Any, Dict, Optional

import requests

from .config import settings


class PolymarketClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or settings.POLYMARKET_GAMMA_BASE_URL).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _build_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        response = self.session.get(self._build_url(path), params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def public_search(
        self,
        query: str,
        limit_per_type: int = 200,
        events_status: str = "active",
    ) -> Dict[str, Any]:
        data = self.get(
            "/public-search",
            params={
                "q": query,
                "limit_per_type": limit_per_type,
                "events_status": events_status,
            },
        )
        if not isinstance(data, dict):
            raise ValueError("Unexpected Polymarket public-search response format")
        return data
