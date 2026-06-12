"""Worker que entrega el outbox de notificaciones de forma periódica.

Puede ejecutarse de dos maneras (ver DECISION_LOG ADR-15):

- **En proceso**, lanzado en el `lifespan` de FastAPI. Es el modo por defecto y basta para
  1..pocas réplicas.
- **Como proceso dedicado**: ``python -m app.infrastructure.outbox_worker``. Recomendado
  con muchas réplicas, para no multiplicar el polling de la tabla; en ese caso se desactiva
  el worker en proceso con ``RUN_OUTBOX_WORKER_IN_PROCESS=false``.

El claim seguro con ``SELECT ... FOR UPDATE SKIP LOCKED`` hace que cualquier número de
workers (en proceso y/o dedicados) entregue cada mensaje una sola vez.
"""

import asyncio
import logging
import signal

from app.application.outbox_service import OutboxProcessor
from app.infrastructure.config import get_settings
from app.infrastructure.db.repositories import SqlAlchemyOutboxRepository
from app.infrastructure.db.session import get_engine, get_sessionmaker
from app.infrastructure.logging_config import configure_logging
from app.infrastructure.notifications import build_notifier

logger = logging.getLogger("app.outbox.worker")

POLL_INTERVAL_SECONDS = 2.0


async def process_outbox_once() -> int:
    """Procesa una tanda de mensajes pendientes con una sesión propia.

    El worker es la **unidad de trabajo** del outbox: el repositorio solo hace ``flush``,
    así que aquí se hace **un commit por tanda**. Eso mantiene el lock de
    `FOR UPDATE SKIP LOCKED` hasta persistir los marcados (entrega segura con varias
    réplicas) y deja los mensajes en ``pending`` si la tanda falla (reintento posterior).
    """
    notifier = build_notifier(get_settings())
    async with get_sessionmaker()() as session:
        processor = OutboxProcessor(
            outbox_repository=SqlAlchemyOutboxRepository(session), notifier=notifier
        )
        count = await processor.process_pending()
        await session.commit()
        return count


async def run_outbox_worker(
    stop_event: asyncio.Event, interval: float = POLL_INTERVAL_SECONDS
) -> None:
    logger.info("Outbox worker iniciado (intervalo %ss)", interval)
    while not stop_event.is_set():
        try:
            await process_outbox_once()
        except Exception:  # un fallo de una tanda no debe matar el worker
            logger.exception("Error en el ciclo del outbox worker")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass
    logger.info("Outbox worker detenido")


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    """Convierte SIGINT/SIGTERM en una parada limpia del bucle (drena la tanda en curso)."""

    def _request_stop() -> None:
        logger.info("Señal de parada recibida; deteniendo el worker del outbox...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows no soporta add_signal_handler; degradar a signal.signal (suficiente
            # para desarrollo local, ya que el worker dedicado se despliega en Linux).
            signal.signal(sig, lambda *_: _request_stop())


async def _serve() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop_event = asyncio.Event()
    _install_signal_handlers(asyncio.get_running_loop(), stop_event)
    logger.info("Worker del outbox dedicado iniciado")
    try:
        await run_outbox_worker(stop_event)
    finally:
        await get_engine().dispose()


def main() -> None:
    """Punto de entrada del worker dedicado: ``python -m app.infrastructure.outbox_worker``."""
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
