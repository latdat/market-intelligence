import asyncio

from market_intelligence.connectors.request_rate_limiter import RequestRateLimiter
from market_intelligence.source_registry import RateLimitConfig


def test_null_rate_limit_never_sleeps() -> None:
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    limiter = RequestRateLimiter(None, sleep=sleep, clock=lambda: 0.0)
    asyncio.run(limiter.wait())
    asyncio.run(limiter.wait())

    assert delays == []


def test_declared_request_window_spaces_every_attempt() -> None:
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    limiter = RequestRateLimiter(
        RateLimitConfig(max_requests=2, period_seconds=10),
        sleep=sleep,
        clock=lambda: 0.0,
    )

    async def run() -> None:
        await limiter.wait()
        await limiter.wait()
        await limiter.wait()

    asyncio.run(run())

    assert delays == [5.0, 10.0]
