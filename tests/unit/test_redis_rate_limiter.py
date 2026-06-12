"""Tests del limitador de ventana deslizante en Redis (con `fakeredis`).

Mismas garantías que la variante en memoria: deja pasar hasta el umbral, bloquea con 429 y
`Retry-After`, desliza la ventana al avanzar el reloj, aísla por clave y se deshabilita con
``max_attempts=0``. Se usa un reloj inyectable para controlar el tiempo de forma determinista.
"""

import pytest
from fakeredis import aioredis
from fastapi import FastAPI

from app.domain.exceptions import RateLimitError
from app.infrastructure.config import Settings
from app.infrastructure.security.rate_limiter import InMemorySlidingWindowRateLimiter
from app.infrastructure.security.redis_rate_limiter import RedisSlidingWindowRateLimiter
from app.web.main import _build_rate_limiter

_SECRET = "x" * 32


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def redis() -> aioredis.FakeRedis:
    return aioredis.FakeRedis()


async def test_allows_up_to_limit_then_blocks(redis: aioredis.FakeRedis) -> None:
    clock = _Clock()
    limiter = RedisSlidingWindowRateLimiter(
        redis=redis, max_attempts=3, window_seconds=60, clock=clock
    )
    for _ in range(3):
        await limiter.check("ip-a")
        clock.now += 1
    with pytest.raises(RateLimitError) as exc_info:
        await limiter.check("ip-a")
    assert exc_info.value.retry_after is not None and exc_info.value.retry_after > 0


async def test_window_slides(redis: aioredis.FakeRedis) -> None:
    clock = _Clock()
    limiter = RedisSlidingWindowRateLimiter(
        redis=redis, max_attempts=2, window_seconds=10, clock=clock
    )
    await limiter.check("ip-b")
    clock.now += 1
    await limiter.check("ip-b")
    with pytest.raises(RateLimitError):
        await limiter.check("ip-b")
    # Avanza más allá de la ventana: los intentos viejos caducan y vuelve a permitir.
    clock.now += 20
    await limiter.check("ip-b")


async def test_blocked_attempt_is_not_counted(redis: aioredis.FakeRedis) -> None:
    clock = _Clock()
    limiter = RedisSlidingWindowRateLimiter(
        redis=redis, max_attempts=1, window_seconds=60, clock=clock
    )
    await limiter.check("ip-c")
    for _ in range(5):  # martillar estando bloqueado no infla la ventana
        with pytest.raises(RateLimitError):
            await limiter.check("ip-c")
    assert await redis.zcard("ratelimit:login:ip-c") == 1


async def test_keys_are_isolated(redis: aioredis.FakeRedis) -> None:
    limiter = RedisSlidingWindowRateLimiter(redis=redis, max_attempts=1, window_seconds=60)
    await limiter.check("ip-x")
    with pytest.raises(RateLimitError):
        await limiter.check("ip-x")
    await limiter.check("ip-y")  # otra clave tiene su propio contador


async def test_disabled_when_max_is_zero(redis: aioredis.FakeRedis) -> None:
    limiter = RedisSlidingWindowRateLimiter(redis=redis, max_attempts=0, window_seconds=60)
    for _ in range(50):
        await limiter.check("ip-z")  # nunca bloquea


def test_build_rate_limiter_defaults_to_memory() -> None:
    settings = Settings(jwt_secret_key=_SECRET)
    limiter = _build_rate_limiter(FastAPI(), settings)
    assert isinstance(limiter, InMemorySlidingWindowRateLimiter)


async def test_build_rate_limiter_selects_redis() -> None:
    settings = Settings(
        jwt_secret_key=_SECRET,
        rate_limiter_backend="redis",
        redis_url="redis://localhost:6379/0",
    )
    app = FastAPI()
    limiter = _build_rate_limiter(app, settings)  # no conecta hasta el primer comando
    try:
        assert isinstance(limiter, RedisSlidingWindowRateLimiter)
        assert app.state.redis_client is not None
    finally:
        await app.state.redis_client.aclose()
