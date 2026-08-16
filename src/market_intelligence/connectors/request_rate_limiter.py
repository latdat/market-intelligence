"""Small per-fetch request pacing for source-declared rate limits."""

import asyncio
import time
from collections.abc import Awaitable, Callable

from market_intelligence.source_registry import RateLimitConfig

type SleepFunction = Callable[[float], Awaitable[None]]
type MonotonicClock = Callable[[], float]


class RequestRateLimiter:
    """Conservatively space requests within one connector fetch."""

    def __init__(
        self,
        config: RateLimitConfig | None,
        *,
        sleep: SleepFunction = asyncio.sleep,
        clock: MonotonicClock = time.monotonic,
    ) -> None:
        self._interval_seconds = (
            None if config is None else config.period_seconds / config.max_requests
        )
        self._sleep = sleep
        self._clock = clock
        self._next_allowed_at: float | None = None

    async def wait(self) -> None:
        """Wait once when needed, then reserve the next request slot."""
        if self._interval_seconds is None:
            return

        now = self._clock()
        if self._next_allowed_at is not None and now < self._next_allowed_at:
            await self._sleep(self._next_allowed_at - now)
            now = self._next_allowed_at

        self._next_allowed_at = now + self._interval_seconds
