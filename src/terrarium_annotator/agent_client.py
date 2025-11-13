"""HTTP client for the terrarium-agent server."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests
from requests import Response
from requests.exceptions import ConnectionError, RequestException, Timeout


class AgentClientError(Exception):
    """Raised when the terrarium-agent server rejects or fails a request."""


@dataclass
class AgentResponse:
    """Container for agent responses."""

    message: Dict
    raw: Dict


class AgentClient:
    """Small wrapper around the Terrarium Agent HTTP API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        timeout: int = 60,
        max_retries: int = 3,
        session: Optional[requests.Session] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = session or requests.Session()

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.4,
        max_tokens: int = 768,
        model: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
    ) -> AgentResponse:
        """Call `/v1/chat/completions` and return the first choice."""

        payload: Dict[str, object] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if model:
            payload["model"] = model
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        data = self._request_with_retry("POST", "/v1/chat/completions", json=payload)
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise AgentClientError(f"Malformed response: {exc}")

        return AgentResponse(message=message, raw=data)

    def health_check(self) -> bool:
        """Return True if the agent server responds with HTTP 200."""

        try:
            resp = self._session.get(f"{self.base_url}/health", timeout=5)
            return resp.ok
        except RequestException:
            return False

    def _request_with_retry(self, method: str, endpoint: str, **kwargs) -> Dict:
        url = f"{self.base_url}{endpoint}"

        for attempt in range(self.max_retries):
            try:
                response: Response = self._session.request(
                    method,
                    url,
                    timeout=self.timeout,
                    **kwargs,
                )
                if response.status_code >= 500:
                    if attempt < self.max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    raise AgentClientError(f"Server error: {response.text}")
                if response.status_code >= 400:
                    raise AgentClientError(f"Request error ({response.status_code}): {response.text}")

                response.raise_for_status()
                return response.json()
            except Timeout:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise AgentClientError("Request timed out")
            except ConnectionError:
                raise AgentClientError(f"Cannot connect to {self.base_url}")
            except RequestException as exc:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise AgentClientError(f"Request failed: {exc}")

        raise AgentClientError("Exceeded retry budget")
