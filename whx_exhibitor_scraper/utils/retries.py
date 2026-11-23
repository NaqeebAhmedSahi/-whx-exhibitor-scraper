"""Retry helpers with exponential backoff."""

from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

from config import settings

T = TypeVar("T")


def exponential_backoff(attempt: int, base: float | None = None) -> float:
    base = base if base is not None else settings.RETRY_BASE_BACKOFF
    jitter = random.uniform(0, 0.4)
    return (base ** attempt) + jitter


def retry_call(
    func: Callable[[], T],
    *,
    attempts: int,
    on_error: Callable[[Exception, int], None] | None = None,
    should_retry: Callable[[Exception], bool] | None = None,
) -> T:
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 — caller decides retry policy
            last = exc
            if should_retry and not should_retry(exc):
                raise
            if on_error:
                on_error(exc, i)
            if i >= attempts:
                break
            time.sleep(exponential_backoff(i))
    assert last is not None
    raise last
