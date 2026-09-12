"""OpenAI-compatible HTTP chat client (v1 AgentClient shape, rebuilt small).

Works against Moonshot/Kimi-style endpoints, vLLM, llama.cpp servers —
anything POSTing `/v1/chat/completions`. Retry policy: exponential backoff
on 5xx/timeouts/connection errors; 4xx and malformed bodies fail fast.
"""

from __future__ import annotations

import time

import requests

from terrarium_annotator.llm.base import (
    AttemptEvent,
    AttemptObserver,
    ChatClientError,
    ChatResponse,
    parse_choice,
)


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        session: requests.Session | None = None,
        attempt_observer: AttemptObserver | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        # Accept either the server root (http://host:port) or an API base
        # already ending in /v1 (e.g. https://api.kimi.com/coding/v1).
        self._endpoint = (
            f"{self.base_url}/chat/completions"
            if self.base_url.endswith("/v1")
            else f"{self.base_url}/v1/chat/completions"
        )
        self._session = session or requests.Session()
        # Read at call time: InstrumentedClient (re)wires it per call.
        self.attempt_observer = attempt_observer
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 2048,
    ) -> ChatResponse:
        payload: dict = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.model:
            payload["model"] = self.model
        if tools:
            payload["tools"] = tools

        last_error: str | None = None
        for attempt in range(1, self.max_retries + 1):
            t0 = time.monotonic()
            try:
                resp = self._session.post(
                    self._endpoint,
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                self._observe(attempt, "error", type(exc).__name__, t0)
                last_error = str(exc)
            else:
                if resp.status_code < 500:
                    if resp.status_code != 200:  # 4xx fails fast
                        self._observe(attempt, "error", f"HTTP{resp.status_code}", t0)
                        raise ChatClientError(
                            f"HTTP {resp.status_code}: {resp.text[:200]}"
                        )
                    try:
                        body = resp.json()
                    except ValueError as exc:
                        self._observe(attempt, "error", "NonJSONResponse", t0)
                        raise ChatClientError(f"non-JSON response body: {exc}") from exc
                    try:
                        parsed = parse_choice(body)
                    except ChatClientError as exc:
                        # 200 but unusable envelope — the attempt FAILED;
                        # observe before raising so it isn't logged success.
                        self._observe(attempt, "error", type(exc).__name__, t0)
                        raise
                    self._observe(attempt, "success", None, t0)
                    return parsed
                self._observe(attempt, "error", f"HTTP{resp.status_code}", t0)
                last_error = f"HTTP {resp.status_code}"
            if attempt < self.max_retries:
                time.sleep(0.5 * (2 ** (attempt - 1)))
        raise ChatClientError(
            f"chat failed after {self.max_retries} attempts: {last_error}"
        )

    def _observe(
        self, attempt: int, status: str, error_type: str | None, t0: float
    ) -> None:
        if self.attempt_observer is not None:
            self.attempt_observer(
                AttemptEvent(
                    attempt=attempt,
                    status=status,
                    error_type=error_type,
                    duration_s=time.monotonic() - t0,
                )
            )
