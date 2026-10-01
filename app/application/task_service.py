"""Casos de uso de tareas y de asignación de responsable.

El acceso se autoriza por **rol de pertenencia** a la lista (ver DECISION_LOG ADR-14):
las lecturas requieren `viewer`; las escrituras, `editor`. No ser miembro hace la lista
indistinguible de inexistente (404); serlo con rol insuficiente devuelve 403. Los métodos
que pueden fijar un responsable devuelven, junto a la tarea, el usuario a **notificar**
(o ``None``), para que la capa web programe el aviso (ver ADR-9).
"""

from app.application.completion import completion_percentage
from app.domain.entities import ListMember, Task, User
from app.domain.enums import ListRole, TaskPriority, TaskStatus
from app.domain.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailedError,
)
from app.domain.repositories import (
    IListMemberRepository,
    ITaskRepository,
    IUserRepository,
)


class TaskService:
    """Lógica de negocio de tareas: CRUD, estado, filtros, completitud y asignación."""

    def __init__(
        self,
        *,
        task_repository: ITaskRepository,
        member_repository: IListMemberRepository,
        user_repository: IUserRepository,
    ) -> None:
        self._tasks = task_repository
        self._members = member_repository
        self._users = user_repository

    # -- helpers de autorización y validación ----------------------------------
    async def _require_role(self, list_id: int, user_id: int, minimum: ListRole) -> ListMember:
        membership = await self._members.get(list_id, user_id)
        if membership is None:
            raise NotFoundError(f"Lista {list_id} no encontrada")
        if membership.role.rank < minimum.rank:
            raise ForbiddenError(f"Se requiere rol '{minimum}' sobre la lista {list_id}")
        return membership

    async def _ensure_user_exists(self, user_id: int) -> User:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError(f"Usuario {user_id} no encontrado")
        return user

    async def _ensure_assignee(self, list_id: int, assignee_id: int) -> User:
        """Valida el responsable de una tarea: debe **existir** y ser **miembro** de la lista.

        Un responsable debe poder ver la lista para hacerse cargo de su tarea, así que solo
        se puede asignar a un colaborador existente (modelo estándar tipo GitHub/Jira: el
        asignado necesita acceso de lectura). La pertenencia la controla el `owner`
        (ADR-14); asignar **no** concede acceso por sí mismo, de modo que un `editor` no
        pueda sumar miembros de forma indirecta (evita una escalada de privilegios). Para
        asignar a alguien externo, el `owner` lo añade antes como colaborador. La invitación
        por email queda así dirigida a quien sí puede acceder. Ver DECISION_LOG
        ADR-20 (sustituye la "asignación a terceros" de ADR-3).

        Orden de comprobación: primero la existencia del usuario (404) y luego la pertenencia
        (409), para que un id inexistente siga devolviendo 404 como antes.
        """
        user = await self._ensure_user_exists(assignee_id)
        if await self._members.get(list_id, assignee_id) is None:
            raise ConflictError(
                f"El usuario {assignee_id} no es miembro de la lista {list_id}; "
                "añádelo como colaborador antes de asignarle tareas."
            )
        return user

    async def _get_task_for(
        self, list_id: int, task_id: int, user_id: int, minimum: ListRole
    ) -> Task:
        await self._require_role(list_id, user_id, minimum)
        task = await self._tasks.get(task_id)
        if task is None or task.list_id != list_id:
            raise NotFoundError(f"Tarea {task_id} no encontrada en la lista {list_id}")
        return task

    @staticmethod
    def _require_updated(task: Task | None, list_id: int, task_id: int) -> Task:
        """Garantiza que la escritura encontró la fila (borrado concurrente → 404)."""
        if task is None:
            raise NotFoundError(f"Tarea {task_id} no encontrada en la lista {list_id}")
        return task

    @staticmethod
    def _ensure_version(task: Task, expected_version: int | None) -> None:
        """Concurrencia optimista: rechaza la escritura si la versión actual no es la esperada.

        `expected_version` es la versión que el cliente leyó (cabecera `If-Match`); ``None`` es
        el comodín ``*`` (cualquier versión vigente, ya garantizada por la existencia de la
        tarea). Si no coincide, otro escritor modificó la tarea entre la lectura y esta
        escritura → 412, evitando el *lost update*. La ventana TOCTOU residual (entre esta
        comprobación y el flush) la cubre `version_id_col` en el repositorio. Ver ADR-26.
        """
        if expected_version is not None and task.version != expected_version:
            raise PreconditionFailedError(
                f"La tarea {task.id} cambió (versión actual {task.version}; If-Match esperaba "
                f"{expected_version}). Vuelve a leerla (nuevo ETag) y reintenta."
            )

    # -- casos de uso ----------------------------------------------------------
    async def create(
        self,
        *,
        user_id: int,
        list_id: int,
        title: str,
        description: str | None = None,
        status: TaskStatus = TaskStatus.PENDING,
        priority: TaskPriority = TaskPriority.MEDIUM,
        assignee_id: int | None = None,
    ) -> tuple[Task, User | None]:
        await self._require_role(list_id, user_id, ListRole.EDITOR)
        assignee: User | None = None
        if assignee_id is not None:
            assignee = await self._ensure_assignee(list_id, assignee_id)
        task = await self._tasks.create(
            list_id=list_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
            assignee_id=assignee_id,
        )
        return task, assignee

    async def get(self, list_id: int, task_id: int, user_id: int) -> Task:
        return await self._get_task_for(list_id, task_id, user_id, ListRole.VIEWER)

    async def list_tasks(
        self,
        list_id: int,
        user_id: int,
        *,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        limit: int,
        after_id: int | None,
    ) -> list[Task]:
        await self._require_role(list_id, user_id, ListRole.VIEWER)
        return await self._tasks.list_by_list(
            list_id, status=status, priority=priority, limit=limit, after_id=after_id
        )

    async def list_tasks_with_completion(
        self,
        list_id: int,
        user_id: int,
        *,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        limit: int,
        after_id: int | None,
    ) -> tuple[list[Task], float]:
        """Lista las tareas y el % de completitud de la lista con **una sola**
        comprobación de rol.

        El endpoint de listado necesita ambos datos; antes invocaba `list_tasks` y
        `completion_percentage` por separado y cada uno repetía el chequeo de pertenencia
        (una consulta de membresía redundante por request). Aquí se autoriza una vez.
        """
        await self._require_role(list_id, user_id, ListRole.VIEWER)
        tasks = await self._tasks.list_by_list(
            list_id, status=status, priority=priority, limit=limit, after_id=after_id
        )
        total, done = await self._tasks.completion_stats(list_id)
        return tasks, completion_percentage(total, done)

    async def update(
        self,
        list_id: int,
        task_id: int,
        user_id: int,
        *,
        title: str,
        description: str | None,
        status: TaskStatus,
        priority: TaskPriority,
        assignee_id: int | None,
        expected_version: int | None,
    ) -> tuple[Task, User | None]:
        current = await self._get_task_for(list_id, task_id, user_id, ListRole.EDITOR)
        self._ensure_version(current, expected_version)
        notify: User | None = None
        if assignee_id is not None:
            assignee = await self._ensure_assignee(list_id, assignee_id)
            if assignee_id != current.assignee_id:
                notify = assignee
        updated = await self._tasks.update(
            task_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
            assignee_id=assignee_id,
        )
        return self._require_updated(updated, list_id, task_id), notify

    async def change_status(
        self,
        list_id: int,
        task_id: int,
        user_id: int,
        status: TaskStatus,
        *,
        expected_version: int | None,
    ) -> Task:
        task = await self._get_task_for(list_id, task_id, user_id, ListRole.EDITOR)
        self._ensure_version(task, expected_version)
        updated = await self._tasks.update(
            task_id,
            title=task.title,
            description=task.description,
            status=status,
            priority=task.priority,
            assignee_id=task.assignee_id,
        )
        return self._require_updated(updated, list_id, task_id)

    async def delete(
        self, list_id: int, task_id: int, user_id: int, *, expected_version: int | None
    ) -> None:
        task = await self._get_task_for(list_id, task_id, user_id, ListRole.EDITOR)
        self._ensure_version(task, expected_version)
        deleted = await self._tasks.delete(task_id)
        if not deleted:
            # Borrado concurrente entre la verificación y la escritura: se traduce a 404,
            # de forma coherente con el resto de escrituras (`_require_updated`) y con
            # `TaskListService.delete`.
            raise NotFoundError(f"Tarea {task_id} no encontrada en la lista {list_id}")

    async def assign(
        self,
        list_id: int,
        task_id: int,
        user_id: int,
        assignee_id: int,
        *,
        expected_version: int | None,
    ) -> tuple[Task, User]:
        """Asigna un responsable. Devuelve la tarea y el usuario asignado."""
        task = await self._get_task_for(list_id, task_id, user_id, ListRole.EDITOR)
        self._ensure_version(task, expected_version)
        user = await self._ensure_assignee(list_id, assignee_id)
        updated = await self._tasks.update(
            task_id,
            title=task.title,
            description=task.description,
            status=task.status,
            priority=task.priority,
            assignee_id=assignee_id,
        )
        return self._require_updated(updated, list_id, task_id), user
