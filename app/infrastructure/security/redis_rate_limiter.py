"""Limitador de intentos con **ventana deslizante en Redis** (compartido entre réplicas).

Implementa el puerto `ILoginRateLimiter` igual que la variante en memoria, pero el estado
vive en Redis, de modo que el umbral es **global** entre todas las réplicas (no por
proceso). Es la evolución natural anticipada en DECISION_LOG ADR-17, detrás del mismo
puerto y sin tocar la capa web.

**Algoritmo (ventana deslizante con sorted set).** Por cada clave (p. ej. la IP) se guarda
un *sorted set* cuyos miembros son los intentos y cuyo *score* es la marca de tiempo. En
cada intento, dentro de una **transacción** Redis (`MULTI/EXEC`, atómica en el servidor):
1. `ZREMRANGEBYSCORE` purga los intentos fuera de la ventana,
2. `ZADD` registra el intento actual,
3. `ZCARD` cuenta los intentos vigentes,
4. `EXPIRE` fija un TTL para que las claves inactivas se recolecten solas.
Si el conteo supera el umbral se rechaza (y se retira el intento recién añadido para no
penalizar la ventana con peticiones bloqueadas, igual que la variante en memoria).

Se usa **reloj de pared** (`time.time`), no `monotonic`: las marcas deben ser comparables
entre procesos y reinicios, cosa que `monotonic` no garantiza.
"""

import time
import uuid
from collections.abc import Callable

from redis.asyncio import Redis

from app.domain.exceptions import RateLimitError
from app.domain.security import ILoginRateLimiter


class RedisSlidingWindowRateLimiter(ILoginRateLimiter):
    """Ventana deslizante en Redis. ``max_attempts <= 0`` deshabilita el límite."""

    def __init__(
        self,
        *,
        redis: Redis,
        max_attempts: int,
        window_seconds: float,
        clock: Callable[[], float] = time.time,
        key_prefix: str = "ratelimit:login:",
    ) -> None:
        self._redis = redis
        self._max = max_attempts
        self._window = window_seconds
        self._clock = clock
        self._prefix = key_prefix

    async def check(self, key: str) -> None:
        if self._max <= 0:  # límite deshabilitado por configuración
            return
        now = self._clock()
        window_start = now - self._window
        redis_key = f"{self._prefix}{key}"
        member = f"{now}:{uuid.uuid4().hex}"  # único: evita colisiones en el mismo instante

        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(redis_key, 0, window_start)
            pipe.zadd(redis_key, {member: now})
            pipe.zcard(redis_key)
            pipe.expire(redis_key, int(self._window) + 1)
            results = await pipe.execute()
        count = int(results[2])

        if count > self._max:
            # No se cuenta el intento bloqueado (paridad con la variante en memoria).
            await self._redis.zrem(redis_key, member)
            retry_after = await self._retry_after(redis_key, now)
            raise RateLimitError(
                f"Demasiados intentos; reintenta en ~{retry_after}s.",
                retry_after=retry_after,
            )

    async def _retry_after(self, redis_key: str, now: float) -> int:
        """Segundos hasta que el intento más antiguo salga de la ventana (mínimo 1)."""
        oldest = await self._redis.zrange(redis_key, 0, 0, withscores=True)
        if not oldest:
            return 1
        oldest_score = float(oldest[0][1])
        return max(int(oldest_score + self._window - now) + 1, 1)
