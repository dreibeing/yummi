"""HTTP client for Woolworths web pages with initial state extraction."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

INITIAL_STATE_MARKER = "window.__INITIAL_STATE__ = "


class FetchError(RuntimeError):
    """Raised when we cannot obtain or parse the initial state."""


@dataclass
class ClientConfig:
    max_retries: int = 4
    timeout: float = 20.0
    delay_range: tuple[float, float] = (0.75, 1.5)
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )


class WoolworthsClient:
    """Thin wrapper around httpx with retry, throttling, and JSON extraction."""

    def __init__(self, *, config: Optional[ClientConfig] = None) -> None:
        self.config = config or ClientConfig()
        self._client = httpx.Client(
            headers={
                "User-Agent": self.config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
            timeout=self.config.timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WoolworthsClient":  # pragma: no cover - trivial
        return self

    def __exit__(self, *exc: object) -> None:  # pragma: no cover - trivial
        self.close()

    def fetch_initial_state(self, url: str) -> Dict[str, Any]:
        """Fetch a page and return the decoded `window.__INITIAL_STATE__` JSON."""

        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt < self.config.max_retries:
            attempt += 1
            try:
                response = self._client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:  # network or HTTP failure
                last_exc = exc
                self._sleep_with_jitter(attempt)
                continue

            state = self._extract_initial_state(response.text)
            if state is not None:
                return state

            last_exc = FetchError("Initial state marker missing; likely bot shield page")
            self._sleep_with_jitter(attempt)

        raise FetchError(f"Failed to fetch initial state from {url}") from last_exc

    def _sleep_with_jitter(self, attempt: int) -> None:
        base = random.uniform(*self.config.delay_range)
        backoff = min(3.0, 0.5 * (attempt - 1))
        time.sleep(base + backoff)

    def _extract_initial_state(self, html: str) -> Optional[Dict[str, Any]]:
        idx = html.find(INITIAL_STATE_MARKER)
        if idx == -1:
            return None
        start = idx + len(INITIAL_STATE_MARKER)
        end = html.find("</script>", start)
        if end == -1:
            return None
        payload = html[start:end].strip()
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            # Some responses include a UTF-8 BOM
            try:
                return json.loads(payload.encode("utf-8").decode("utf-8-sig"))
            except json.JSONDecodeError:
                return None
