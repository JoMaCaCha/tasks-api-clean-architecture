"""Procesador del outbox de notificaciones (entrega con reintentos).

Lee los mensajes ``pending`` y los entrega a través del `INotifier`. En caso de fallo,
incrementa el contador de intentos y deja el mensaje ``pending`` para reintentar; tras
agotar ``MAX_ATTEMPTS`` lo marca ``failed``. El encolado es idempotente (clave única), de
modo que un reintento o un reenvío no produce invitaciones duplicadas. Ver ADR-9/ADR-15.
"""

import logging

from app.domain.notifications import INotifier
from app.domain.repositories import IOutboxRepository

logger = logging.getLogger("app.outbox")


class OutboxProcessor:
    """Entrega los mensajes pendientes del outbox."""

    MAX_ATTEMPTS = 5

    def __init__(self, *, outbox_repository: IOutboxRepository, notifier: INotifier) -> None:
        self._outbox = outbox_repository
        self._notifier = notifier

    async def process_pending(self, limit: int = 20) -> int:
        """Procesa hasta ``limit`` mensajes pendientes. Devuelve cuántos intentó entregar.

        No hace ``commit``: el repositorio hace ``flush`` y el commit lo controla quien
        invoca (el worker, por tanda). Así fetch y marcado comparten transacción.
        """
        messages = await self._outbox.fetch_pending(limit)
        for message in messages:
            try:
                await self._notifier.notify_task_assignment(
                    email=message.email, task_title=message.task_title
                )
            except Exception as exc:  # la entrega puede fallar de muchas formas
                logger.warning(
                    "Fallo al entregar la notificación %s (intento %s): %s",
                    message.id,
                    message.attempts + 1,
                    exc,
                )
                if message.attempts + 1 >= self.MAX_ATTEMPTS:
                    await self._outbox.mark_failed(message.id, str(exc))
                else:
                    await self._outbox.mark_retry(message.id, str(exc))
            else:
                await self._outbox.mark_sent(message.id)
        return len(messages)
