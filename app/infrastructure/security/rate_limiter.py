"""Limitador de intentos en memoria (ventana deslizante) para el login/registro.

Implementa el puerto `ILoginRateLimiter` con un algoritmo de **ventana deslizante**: por
cada clave (p. ej. la IP del cliente) se guardan las marcas de tiempo de los intentos
recientes y se rechaza cuando superan el umbral dentro de la ventana.

**Alcance.** El estado vive en el proceso, así que el límite es **por réplica** (cada
worker/instancia cuenta por separado). Es la defensa proporcionada para un despliegue de
un proceso; para varias réplicas, la evolución es un contador compartido en Redis
detrás de este mismo puerto. Ver DECISION_LOG ADR-17.
"""

import time
from collections import deque
from collections.abc import Callable

from app.domain.exceptions import RateLimitError
from app.domain.security import ILoginRateLimiter


class InMemorySlidingWindowRateLimiter(ILoginRateLimiter):
    """Ventana deslizante en memoria. ``max_attempts <= 0`` deshabilita el límite."""

    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_attempts
        self._window = window_seconds
        # `monotonic` (no `time()`): inmune a saltos del reloj del sistema. Inyectable
        # para poder verificar el deslizamiento de la ventana en los tests.
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    async def check(self, key: str) -> None:
        if self._max <= 0:  # límite deshabilitado por configuración
            return
        now = self._clock()
        threshold = now - self._window
        bucket = self._hits.get(key)
        if bucket is not None:
            # Descarta los intentos que ya salieron de la ventana.
            while bucket and bucket[0] <= threshold:
                bucket.popleft()
            if not bucket:
                # No deja cubos vacíos: la memoria queda acotada a las claves activas.
                del self._hits[key]
                bucket = None
        if bucket is not None and len(bucket) >= self._max:
            retry_after = int(bucket[0] + self._window - now) + 1
            raise RateLimitError(
                f"Demasiados intentos; reintenta en ~{retry_after}s.",
                retry_after=retry_after,
            )
        self._hits.setdefault(key, deque()).append(now)
