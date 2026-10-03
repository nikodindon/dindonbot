"""Small OpenAI-compatible HTTP client with model discovery and SSE streaming."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from email.utils import parsedate_to_datetime
import http.client
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


class OpenAIAPIError(RuntimeError):
    """An HTTP error returned by an OpenAI-compatible endpoint."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(f"LLM endpoint returned HTTP {status}: {message}")


class ProtocolError(RuntimeError):
    """The endpoint returned a response outside the supported API shape."""


class OpenAICompatibleClient:
    """Client for /v1/models and /v1/chat/completions.

    Retries are limited to request establishment. A stream that has already
    yielded data is never restarted, avoiding duplicate assistant output.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout_s: float = 120.0,
        max_retries: int = 2,
        retry_backoff_s: float = 0.25,
    ) -> None:
        normalized = base_url.rstrip("/") + "/"
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must use http or https")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, query parameters, or a fragment")
        if timeout_s <= 0 or max_retries < 0 or retry_backoff_s < 0:
            raise ValueError("timeout must be positive and retry settings non-negative")
        self.base_url = normalized
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s

    def list_models(self) -> list[str]:
        """Return model IDs reported by the endpoint's /v1/models route."""
        response = self._request("v1/models", method="GET", accept="application/json")
        try:
            with response:
                document = json.loads(response.read())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("invalid JSON from /v1/models") from exc
        models = document.get("data") if isinstance(document, dict) else None
        if not isinstance(models, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in models
        ):
            raise ProtocolError("/v1/models response must contain a data list of model IDs")
        return [item["id"] for item in models]

    def chat_completions(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        extra: Mapping[str, Any] | None = None,
        stream: bool = False,
    ) -> dict[str, Any] | Iterator[dict[str, Any]]:
        """Create a chat completion, returning raw OpenAI response objects."""
        if not model:
            raise ValueError("model must not be empty")
        if not messages:
            raise ValueError("at least one message is required")
        body: dict[str, Any] = {
            "model": model,
            "messages": [dict(message) for message in messages],
            "stream": stream,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools is not None:
            body["tools"] = [dict(tool) for tool in tools]
        if extra:
            reserved = {"model", "messages", "stream"} & extra.keys()
            if reserved:
                raise ValueError(f"extra cannot override protocol fields: {', '.join(sorted(reserved))}")
            body.update(extra)
        response = self._request(
            "v1/chat/completions",
            method="POST",
            payload=body,
            accept="text/event-stream" if stream else "application/json",
        )
        if stream:
            return self._stream_response(response)
        try:
            with response:
                document = json.loads(response.read())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("invalid JSON from /v1/chat/completions") from exc
        if not isinstance(document, dict) or not isinstance(document.get("choices"), list):
            raise ProtocolError("chat completion response must contain a choices list")
        return document

    def _request(
        self,
        path: str,
        *,
        method: str,
        accept: str,
        payload: Mapping[str, Any] | None = None,
    ):
        url = urljoin(self.base_url, path)
        headers = {"Accept": accept}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(url, data=data, headers=headers, method=method)

        for attempt in range(self.max_retries + 1):
            try:
                return urlopen(request, timeout=self.timeout_s)
            except HTTPError as exc:
                exc.read()
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if retryable and attempt < self.max_retries:
                    self._wait_before_retry(attempt, exc.headers.get("Retry-After"))
                    exc.close()
                    continue
                exc.close()
                raise OpenAIAPIError(exc.code, "request failed") from None
            except (URLError, TimeoutError, http.client.HTTPException) as exc:
                if attempt >= self.max_retries:
                    reason = exc.reason if isinstance(exc, URLError) else str(exc)
                    raise ConnectionError(f"could not reach LLM endpoint: {reason}") from exc
                self._wait_before_retry(attempt)
        raise AssertionError("unreachable retry loop")

    def _wait_before_retry(self, attempt: int, retry_after: str | None = None) -> None:
        delay = None
        if retry_after:
            try:
                delay = max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after).timestamp()
                    delay = max(0.0, retry_at - time.time())
                except (TypeError, ValueError, OverflowError):
                    delay = None
        if delay is None:
            delay = self.retry_backoff_s * (2**attempt)
        time.sleep(delay)

    def _stream_response(self, response) -> Iterator[dict[str, Any]]:
        def chunks() -> Iterator[dict[str, Any]]:
            try:
                data_lines: list[str] = []
                for raw_line in response:
                    line = raw_line.decode("utf-8").rstrip("\r\n")
                    if not line:
                        if data_lines:
                            chunk = self._decode_sse_data("\n".join(data_lines))
                            data_lines.clear()
                            if chunk is None:
                                return
                            yield chunk
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif line.startswith(":"):
                        continue
                if data_lines:
                    chunk = self._decode_sse_data("\n".join(data_lines))
                    if chunk is not None:
                        yield chunk
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProtocolError("invalid SSE data from chat completion endpoint") from exc
            finally:
                response.close()

        return chunks()

    @staticmethod
    def _decode_sse_data(data: str) -> dict[str, Any] | None:
        if data == "[DONE]":
            return None
        document = json.loads(data)
        if not isinstance(document, dict):
            raise ProtocolError("SSE data must be a JSON object")
        return document
