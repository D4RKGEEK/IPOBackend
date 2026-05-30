"""
Tiny retry-with-exponential-backoff helper. Synchronous and async variants.

Designed for use around network I/O (HTTP downloads, LLM calls) where:
  - transient failures are common (timeouts, 5xx, connection reset)
  - permanent failures (4xx) should NOT be retried
  - the caller passes the predicate that decides "is this retriable?"

Usage:
    from app.retry import retry, async_retry

    @retry(attempts=3, base_delay=1.0, retry_on=(httpx.TimeoutException, httpx.ConnectError))
    def download(url): ...

    @async_retry(attempts=3, base_delay=1.0)
    async def upload(...): ...
"""
from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from typing import Any, Callable, Iterable, Type

logger = logging.getLogger(__name__)


def _is_retriable(exc: BaseException, retry_on: Iterable[Type[BaseException]]) -> bool:
    return isinstance(exc, tuple(retry_on))


def _backoff_seconds(attempt: int, base_delay: float, max_delay: float, jitter: float) -> float:
    """Exponential backoff with full jitter — attempt is 0-indexed."""
    raw = min(max_delay, base_delay * (2 ** attempt))
    # Full jitter: random in [0, raw)
    return raw * (1 - jitter) + raw * jitter * random.random()


def retry(
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.5,
    retry_on: Iterable[Type[BaseException]] = (Exception,),
    label: str = "",
) -> Callable:
    """Sync retry decorator.

    Re-raises the original exception after the final failed attempt.
    """
    def decorator(fn: Callable) -> Callable:
        name = label or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc: BaseException | None = None
            for attempt in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except BaseException as exc:
                    last_exc = exc
                    if not _is_retriable(exc, retry_on) or attempt == attempts - 1:
                        raise
                    delay = _backoff_seconds(attempt, base_delay, max_delay, jitter)
                    logger.warning(
                        "[retry] %s attempt %d/%d failed: %s — sleeping %.2fs",
                        name, attempt + 1, attempts, exc, delay,
                    )
                    time.sleep(delay)
            assert last_exc is not None
            raise last_exc
        return wrapper
    return decorator


def async_retry(
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.5,
    retry_on: Iterable[Type[BaseException]] = (Exception,),
    label: str = "",
) -> Callable:
    """Async retry decorator (same semantics as `retry`)."""
    def decorator(fn: Callable) -> Callable:
        name = label or fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc: BaseException | None = None
            for attempt in range(attempts):
                try:
                    return await fn(*args, **kwargs)
                except BaseException as exc:
                    last_exc = exc
                    if not _is_retriable(exc, retry_on) or attempt == attempts - 1:
                        raise
                    delay = _backoff_seconds(attempt, base_delay, max_delay, jitter)
                    logger.warning(
                        "[retry] %s attempt %d/%d failed: %s — sleeping %.2fs",
                        name, attempt + 1, attempts, exc, delay,
                    )
                    await asyncio.sleep(delay)
            assert last_exc is not None
            raise last_exc
        return wrapper
    return decorator


# Convenience: call any callable with retry without decorating it.
def call_with_retry(fn: Callable[..., Any], *args, **kwargs) -> Any:
    """One-shot retry wrapper. Pulls retry params out of kwargs."""
    rkw = {k: kwargs.pop(k) for k in (
        "attempts", "base_delay", "max_delay", "jitter", "retry_on", "label"
    ) if k in kwargs}
    return retry(**rkw)(fn)(*args, **kwargs)
