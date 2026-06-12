"""Enumeraciones del dominio.

Los valores exactos se fijan aquí (el PDF no los especifica). Se usan `str, Enum`
para que serialicen como cadenas estables en la API y en la base de datos.
"""

from enum import StrEnum


class TaskStatus(StrEnum):
    """Estado del ciclo de vida de una tarea."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class TaskPriority(StrEnum):
    """Prioridad de una tarea."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ListRole(StrEnum):
    """Rol de un usuario sobre una lista (control de acceso por colaborador).

    Orden de privilegio: ``viewer`` < ``editor`` < ``owner``. Ver DECISION_LOG ADR-14.
    """

    VIEWER = "viewer"
    EDITOR = "editor"
    OWNER = "owner"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]


_ROLE_RANK: dict[ListRole, int] = {
    ListRole.VIEWER: 1,
    ListRole.EDITOR: 2,
    ListRole.OWNER: 3,
}


class OutboxStatus(StrEnum):
    """Estado de un mensaje del outbox de notificaciones."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
