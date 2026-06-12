"""Tests del limitador de intentos en memoria (ventana deslizante)."""

import pytest

from app.domain.exceptions import RateLimitError
from app.infrastructure.security.rate_limiter import InMemorySlidingWindowRateLimiter


async def test_allows_up_to_max_then_blocks() -> None:
    limiter = InMemorySlidingWindowRateLimiter(max_attempts=3, window_seconds=60)
    for _ in range(3):
        await limiter.check("login:1.2.3.4")
    with pytest.raises(RateLimitError) as exc:
        await limiter.check("login:1.2.3.4")
    # El error informa cuándo reintentar (cabecera Retry-After).
    assert exc.value.retry_after is not None and exc.value.retry_after > 0
    assert exc.value.status_code == 429


async def test_keys_are_independent() -> None:
    limiter = InMemorySlidingWindowRateLimiter(max_attempts=1, window_seconds=60)
    await limiter.check("login:a")
    await limiter.check("login:b")  # otra clave: no se ve afectada
    with pytest.raises(RateLimitError):
        await limiter.check("login:a")


async def test_window_slides_and_allows_again() -> None:
    now = [100.0]
    limiter = InMemorySlidingWindowRateLimiter(
        max_attempts=2, window_seconds=10, clock=lambda: now[0]
    )
    await limiter.check("login:x")
    await limiter.check("login:x")
    with pytest.raises(RateLimitError):
        await limiter.check("login:x")
    now[0] += 11  # la ventana de 10s ya pasó: los intentos viejos caducan
    await limiter.check("login:x")  # vuelve a permitir


async def test_zero_max_disables_the_limit() -> None:
    limiter = InMemorySlidingWindowRateLimiter(max_attempts=0, window_seconds=60)
    for _ in range(50):
        await limiter.check("login:anything")  # nunca lanza
