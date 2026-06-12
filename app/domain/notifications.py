"""Puerto de notificaciones.

Abstracción para el envío (simulado) de notificaciones por email. La implementación
concreta vive en `infrastructure`. La entrega no es directa: la asignación de un
responsable **encola** la invitación en un outbox transaccional y un worker la entrega
a través de este puerto con reintentos e idempotencia (ver DECISION_LOG ADR-9 y ADR-15).
"""

from abc import ABC, abstractmethod


class INotifier(ABC):
    """Notificador de eventos de negocio."""

    @abstractmethod
    async def notify_task_assignment(self, *, email: str, task_title: str) -> None:
        """Notifica a un usuario que se le asignó una tarea."""
        ...
