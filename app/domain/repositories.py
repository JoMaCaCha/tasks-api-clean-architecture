"""Interfaces (puertos) de los repositorios.

La capa `application` depende solo de estas abstracciones; `infrastructure` las
implementa. Así se invierte la dependencia (DIP) y los casos de uso son testeables
con dobles de prueba.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.entities import (
    ListMember,
    OutboxMessage,
    RefreshToken,
    Task,
    TaskList,
    User,
)
from app.domain.enums import ListRole, TaskPriority, TaskStatus


class ITaskListRepository(ABC):
    """Persistencia de listas de tareas.

    La autorización (quién puede ver/editar) se resuelve por **pertenencia** en
    `IListMemberRepository`; estos métodos operan por `id` y el servicio comprueba el rol
    antes de invocarlos. Los listados usan **paginación por keyset** (`after_id`).
    """

    @abstractmethod
    async def create(self, *, owner_id: int, title: str, description: str | None) -> TaskList: ...

    @abstractmethod
    async def get(self, list_id: int) -> TaskList | None: ...

    @abstractmethod
    async def list_for_member(
        self, user_id: int, *, limit: int, after_id: int | None
    ) -> list[TaskList]:
        """Listas donde el usuario es miembro, paginadas por keyset (`id > after_id`)."""
        ...

    @abstractmethod
    async def update(
        self, list_id: int, *, title: str, description: str | None
    ) -> TaskList | None: ...

    @abstractmethod
    async def delete(self, list_id: int) -> bool: ...


class IListMemberRepository(ABC):
    """Pertenencia de usuarios a listas, con rol (control de acceso por colaborador)."""

    @abstractmethod
    async def add(self, *, list_id: int, user_id: int, role: ListRole) -> ListMember: ...

    @abstractmethod
    async def get(self, list_id: int, user_id: int) -> ListMember | None: ...

    @abstractmethod
    async def list_for_list(
        self, list_id: int, *, limit: int, after_user_id: int | None
    ) -> list[ListMember]:
        """Miembros de una lista, ordenados y paginados por keyset (`user_id > after_user_id`)."""
        ...

    @abstractmethod
    async def set_role(self, list_id: int, user_id: int, role: ListRole) -> ListMember | None: ...

    @abstractmethod
    async def remove(self, list_id: int, user_id: int) -> bool: ...


class ITaskRepository(ABC):
    """Persistencia de tareas."""

    @abstractmethod
    async def create(
        self,
        *,
        list_id: int,
        title: str,
        description: str | None,
        status: TaskStatus,
        priority: TaskPriority,
        assignee_id: int | None,
    ) -> Task: ...

    @abstractmethod
    async def get(self, task_id: int) -> Task | None: ...

    @abstractmethod
    async def list_by_list(
        self,
        list_id: int,
        *,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        limit: int,
        after_id: int | None,
    ) -> list[Task]:
        """Tareas de una lista (con filtros), paginadas por keyset (`id > after_id`)."""
        ...

    @abstractmethod
    async def completion_stats(self, list_id: int) -> tuple[int, int]:
        """Devuelve ``(total, completadas)`` de una lista para el % de completitud.

        Se expone como operación del repositorio para que el conteo se resuelva en la
        base de datos (agregación) en lugar de materializar todas las tareas.
        """
        ...

    @abstractmethod
    async def update(
        self,
        task_id: int,
        *,
        title: str,
        description: str | None,
        status: TaskStatus,
        priority: TaskPriority,
        assignee_id: int | None,
    ) -> Task | None: ...

    @abstractmethod
    async def delete(self, task_id: int) -> bool: ...

    @abstractmethod
    async def clear_assignee_in_list(self, list_id: int, user_id: int) -> int:
        """Desasigna (``assignee_id`` → ``NULL``) las tareas de la lista cuyo responsable es
        ``user_id``. Devuelve cuántas se desasignaron.

        Lo invoca el servicio al **quitar a un colaborador**, para preservar la invariante de
        ADR-20 (el responsable de una tarea debe ser miembro de la lista): un no-miembro no
        puede seguir figurando como responsable. Es el equivalente, dentro de la unidad de
        trabajo, al ``ON DELETE SET NULL`` del FK ``assignee_id`` cuando se elimina el usuario.
        """
        ...


class IUserRepository(ABC):
    """Persistencia de usuarios."""

    @abstractmethod
    async def create(self, *, email: str, hashed_password: str) -> User: ...

    @abstractmethod
    async def get(self, user_id: int) -> User | None: ...

    @abstractmethod
    async def get_by_email(self, email: str) -> User | None: ...

    @abstractmethod
    async def bump_token_version(self, user_id: int) -> int:
        """Incrementa la versión de token del usuario y devuelve el nuevo valor."""
        ...


class IRefreshTokenRepository(ABC):
    """Persistencia de refresh tokens (solo se guarda el hash)."""

    @abstractmethod
    async def add(self, *, user_id: int, token_hash: str, expires_at: datetime) -> RefreshToken: ...

    @abstractmethod
    async def get_by_hash(self, token_hash: str) -> RefreshToken | None: ...

    @abstractmethod
    async def revoke(self, token_hash: str) -> None: ...

    @abstractmethod
    async def revoke_all_for_user(self, user_id: int) -> None: ...

    @abstractmethod
    async def delete_expired(self, *, now: datetime) -> int:
        """Elimina los refresh tokens ya expirados (``expires_at < now``).

        Devuelve cuántas filas se borraron. Solo afecta a tokens **ya vencidos** (revocados
        o no), de modo que la detección de reúso sobre tokens aún vigentes (ADR-13) queda
        intacta: un refresh válido —o uno revocado dentro de su ventana— nunca se purga. Lo
        invoca un trabajo de mantenimiento periódico para acotar el crecimiento de la tabla
        (ver DECISION_LOG ADR-21).
        """
        ...


class IOutboxRepository(ABC):
    """Outbox de notificaciones: encolado idempotente y entrega con reintentos."""

    @abstractmethod
    async def enqueue(self, *, idempotency_key: str, email: str, task_title: str) -> bool:
        """Encola un mensaje. Devuelve ``False`` si la clave ya existía (idempotencia)."""
        ...

    @abstractmethod
    async def fetch_pending(self, limit: int) -> list[OutboxMessage]:
        """Mensajes aún por entregar (estado ``pending``), ordenados por antigüedad."""
        ...

    @abstractmethod
    async def mark_sent(self, message_id: int) -> None: ...

    @abstractmethod
    async def mark_retry(self, message_id: int, error: str) -> None:
        """Incrementa intentos y guarda el error; el mensaje sigue ``pending``."""
        ...

    @abstractmethod
    async def mark_failed(self, message_id: int, error: str) -> None:
        """Marca como ``failed`` tras agotar los reintentos."""
        ...
