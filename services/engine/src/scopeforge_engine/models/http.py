"""Shared guarded HTTP: timeouts, retry-with-backoff on safe failures only,
Retry-After respect, and sanitized error mapping. Cancellation propagates
(anyio scopes) — never swallowed here."""
from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

import httpx

from .errors import ProviderError

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
MAX_RETRY_AFTER_S = 30.0


def classify_http_error(provider: str, exc: Exception, *, attempts: int) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(kind="timeout", provider=provider, retryable=False,
                             message=f"request timed out after {attempts} attempt(s)")
    if isinstance(exc, httpx.NetworkError | httpx.TransportError):
        return ProviderError(kind="network", provider=provider, retryable=False,
                             message=f"network error: {type(exc).__name__}")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return ProviderError(kind="auth", provider=provider, status=status, retryable=False,
                                 message=f"authentication rejected (HTTP {status}); check credential ref")
        if status == 404:
            return ProviderError(kind="bad_request", provider=provider, status=status,
                                 retryable=False, message="endpoint not found (HTTP 404)")
        if status == 400:
            return ProviderError(kind="bad_request", provider=provider, status=status,
                                 retryable=False, message="request rejected (HTTP 400)")
        if status in RETRYABLE_STATUS:
            return ProviderError(kind="rate_limited" if status == 429 else "overloaded",
                                 provider=provider, status=status, retryable=True,
                                 message=f"HTTP {status} after {attempts} attempt(s)")
        return ProviderError(kind="server", provider=provider, status=status, retryable=False,
                             message=f"HTTP {status}")
    return ProviderError(kind="unknown", provider=provider, retryable=False,
                         message=f"{type(exc).__name__}")


def retry_after_s(response: httpx.Response, attempt: int, base_s: float = 0.5) -> float:
    try:
        ra = float(response.headers.get("retry-after", ""))
        return min(max(ra, 0.0), MAX_RETRY_AFTER_S)
    except ValueError:
        return min(base_s * (2**attempt) + random.uniform(0, 0.25), MAX_RETRY_AFTER_S)


async def send_with_retry(
    client: httpx.AsyncClient,
    build: Callable[[], httpx.Request],
    *,
    provider: str,
    retries: int = 2,
    snooze: Callable[[float], Any] | None = None,
) -> httpx.Response:
    """Build+send, retrying only safe failures (timeout/429/5xx). Non-idempotent
    callers keep retries=0. Raises classified ProviderError (sanitized)."""
    import anyio

    sleep = snooze or anyio.sleep
    last: Exception | None = None
    for attempt in range(retries + 1):
        request = build()  # build once per attempt (attempt counting + no duplicate side effects)
        try:
            response = await client.send(request, stream=True)
            if response.status_code < 400:
                return response
            if response.status_code in RETRYABLE_STATUS and attempt < retries:
                await response.aclose()
                await sleep(retry_after_s(response, attempt))
                continue
            await response.aclose()
            raise classify_http_error(
                provider, httpx.HTTPStatusError("error", request=request, response=response),
                attempts=attempt + 1)
        except httpx.TimeoutException as exc:
            last = exc
            if attempt < retries:
                await sleep(min(0.5 * (2**attempt) + random.uniform(0, 0.25), 10.0))
                continue
            raise classify_http_error(provider, exc, attempts=attempt + 1) from None
        except httpx.NetworkError as exc:
            last = exc
            if attempt < retries:
                await sleep(min(0.5 * (2**attempt), 5.0))
                continue
            raise classify_http_error(provider, exc, attempts=attempt + 1) from None
    assert last is not None
    raise classify_http_error(provider, last, attempts=retries + 1)
