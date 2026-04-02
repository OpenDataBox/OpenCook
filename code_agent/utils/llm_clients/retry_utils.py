# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

import logging
import random
import threading
import time
import traceback
from functools import wraps
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_with(
    func: Callable[..., T],
    provider_name: str = "OpenAI",
    max_retries: int = 3,
    cancel_event: threading.Event | None = None,
    should_retry: Callable[[Exception], bool] | None = None,
) -> Callable[..., T]:
    """Retry logic with randomized backoff.

    Args:
        func: The function to decorate.
        provider_name: Name shown in log messages.
        max_retries: Maximum number of retry attempts.
        cancel_event: When set, the sleep is interrupted and no further
                      retries are attempted.
        should_retry: Provider-supplied predicate.  Return False for
                      permanent errors (400, 401, …) to re-raise immediately
                      without sleeping.  None means always retry.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        last_exception: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e

                if attempt == max_retries:
                    raise

                if cancel_event is not None and cancel_event.is_set():
                    raise

                if should_retry is not None and not should_retry(e):
                    raise

                sleep_time = random.randint(3, 30)
                logger.warning(
                    "%s API call failed: %s. Will sleep for %d seconds and retry.\n%s",
                    provider_name, e, sleep_time, traceback.format_exc(),
                )
                deadline = time.monotonic() + sleep_time
                while time.monotonic() < deadline:
                    if cancel_event is not None and cancel_event.is_set():
                        raise last_exception  # type: ignore[misc]
                    time.sleep(min(1.0, deadline - time.monotonic()))

        raise last_exception or Exception("Retry failed for unknown reason")

    return wrapper
