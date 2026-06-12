"""Mantenimiento periódico en segundo plano: purga de refresh tokens expirados.

La tabla `refresh_tokens` crece con cada login/refresh y, sin limpieza, acumula filas
vencidas indefinidamente (más almacenamiento, índices más grandes y mayor superficie de
datos sensibles en reposo). RFC 9700 (BCP de seguridad OAuth 2.0, enero 2025) y la guía
OWASP recomiendan **un trabajo en segundo plano** que elimine los tokens expirados —no
hacerlo en la ruta de la petición, para no añadir latencia ni contención al hot path.

Se ejecuta dentro del proceso de la API (en el `lifespan`, junto al worker del outbox) y
es configurable (`RUN_TOKEN_CLEANUP_IN_PROCESS`, `TOKEN_CLEANUP_INTERVAL_SECONDS`). La
purga es **idempotente** (un `DELETE WHERE expires_at < now`), así que ejecutarla en varias
réplicas es inofensivo (la segunda no encuentra nada). Ver DECISION_LOG ADR-21.
"""

import asyncio
import logging
from datetime import UTC, datetime

from app.infrastructure.db.repositories import SqlAlchemyRefreshTokenRepository
from app.infrastructure.db.session import get_sessionmaker

logger = logging.getLogger("app.maintenance")

# Cadencia por defecto del barrido (1 h). Los tokens expirados no requieren borrado
# inmediato (ya no son utilizables), así que una ventana amplia basta y minimiza la carga.
DEFAULT_CLEANUP_INTERVAL_SECONDS = 3600.0


async def cleanup_expired_refresh_tokens_once() -> int:
    """Borra los refresh tokens ya expirados en una unidad de trabajo propia.

    Abre una sesión, delega en el repositorio el `DELETE` y hace **un commit**. Devuelve
    cuántos eliminó. Solo afecta a `expires_at < ahora`, de modo que la detección de reúso
    sobre tokens vigentes (revocados o no) se mantiene intacta (ADR-13/ADR-21).
    """
    async with get_sessionmaker()() as session:
        repo = SqlAlchemyRefreshTokenRepository(session)
        removed = await repo.delete_expired(now=datetime.now(UTC))
        await session.commit()
    if removed:
        logger.info("Purga de refresh tokens: %s expirados eliminados", removed)
    return removed


async def run_token_cleanup_worker(
    stop_event: asyncio.Event, interval: float = DEFAULT_CLEANUP_INTERVAL_SECONDS
) -> None:
    """Bucle de purga periódica hasta recibir la señal de parada (`stop_event`)."""
    logger.info("Worker de limpieza de tokens iniciado (intervalo %ss)", interval)
    while not stop_event.is_set():
        try:
            await cleanup_expired_refresh_tokens_once()
        except Exception:  # un fallo de una pasada no debe matar el worker
            logger.exception("Error en el ciclo de limpieza de refresh tokens")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass
    logger.info("Worker de limpieza de tokens detenido")
