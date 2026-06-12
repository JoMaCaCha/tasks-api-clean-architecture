"""Casos de uso de listas de tareas (§1.a.i) con control de acceso por colaborador.

La autorización es por **rol de pertenencia** (ver DECISION_LOG ADR-14): no ser miembro
de una lista la hace indistinguible de inexistente (404); serlo con rol insuficiente
devuelve 403. Quien crea una lista queda como `owner`. Los listados usan paginación por
keyset.
"""

from app.application.completion import completion_percentage
from app.domain.entities import ListMember, TaskList
from app.domain.enums import ListRole
from app.domain.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.domain.repositories import (
    IListMemberRepository,
    ITaskListRepository,
    ITaskRepository,
    IUserRepository,
)


class TaskListService:
    """CRUD de listas y gestión de colaboradores, autorizado por rol."""

    def __init__(
        self,
        *,
        repository: ITaskListRepository,
        member_repository: IListMemberRepository,
        user_repository: IUserRepository,
        task_repository: ITaskRepository,
    ) -> None:
        self._repository = repository
        self._members = member_repository
        self._users = user_repository
        # Se usa para la agregación de completitud (read model de la lista) y para desasignar
        # las tareas de un colaborador al expulsarlo (`remove_member`, invariante de ADR-20);
        # las demás escrituras de tareas siguen viviendo en `TaskService`.
        self._tasks = task_repository

    async def _require_role(self, list_id: int, user_id: int, minimum: ListRole) -> ListMember:
        membership = await self._members.get(list_id, user_id)
        if membership is None:
            raise NotFoundError(f"Lista {list_id} no encontrada")
        if membership.role.rank < minimum.rank:
            raise ForbiddenError(f"Se requiere rol '{minimum}' sobre la lista {list_id}")
        return membership

    # -- CRUD de listas --------------------------------------------------------
    async def create(
        self, *, owner_id: int, title: str, description: str | None = None
    ) -> TaskList:
        task_list = await self._repository.create(
            owner_id=owner_id, title=title, description=description
        )
        await self._members.add(list_id=task_list.id, user_id=owner_id, role=ListRole.OWNER)
        return task_list

    async def get(self, list_id: int, user_id: int) -> TaskList:
        await self._require_role(list_id, user_id, ListRole.VIEWER)
        task_list = await self._repository.get(list_id)
        if task_list is None:
            raise NotFoundError(f"Lista {list_id} no encontrada")
        return task_list

    async def get_with_completion(self, list_id: int, user_id: int) -> tuple[TaskList, float]:
        """Devuelve la lista y su % de completitud con **una sola** comprobación de rol.

        El detalle de la lista necesita ambos datos; antes el endpoint llamaba a `get` y a
        `TaskService.completion_percentage` por separado y cada uno repetía el chequeo de
        pertenencia (una consulta de membresía redundante). Aquí se autoriza una vez.
        """
        await self._require_role(list_id, user_id, ListRole.VIEWER)
        task_list = await self._repository.get(list_id)
        if task_list is None:
            raise NotFoundError(f"Lista {list_id} no encontrada")
        total, done = await self._tasks.completion_stats(list_id)
        return task_list, completion_percentage(total, done)

    async def list_all(self, user_id: int, *, limit: int, after_id: int | None) -> list[TaskList]:
        return await self._repository.list_for_member(user_id, limit=limit, after_id=after_id)

    async def update(
        self, list_id: int, user_id: int, *, title: str, description: str | None = None
    ) -> TaskList:
        await self._require_role(list_id, user_id, ListRole.EDITOR)
        updated = await self._repository.update(list_id, title=title, description=description)
        if updated is None:
            raise NotFoundError(f"Lista {list_id} no encontrada")
        return updated

    async def delete(self, list_id: int, user_id: int) -> None:
        await self._require_role(list_id, user_id, ListRole.OWNER)
        deleted = await self._repository.delete(list_id)
        if not deleted:
            raise NotFoundError(f"Lista {list_id} no encontrada")

    # -- Gestión de colaboradores ---------------------------------------------
    async def list_members(
        self, list_id: int, user_id: int, *, limit: int, after_user_id: int | None
    ) -> list[ListMember]:
        await self._require_role(list_id, user_id, ListRole.VIEWER)
        return await self._members.list_for_list(list_id, limit=limit, after_user_id=after_user_id)

    async def add_member(
        self, list_id: int, requester_id: int, *, user_id: int, role: ListRole
    ) -> ListMember:
        await self._require_role(list_id, requester_id, ListRole.OWNER)
        if await self._users.get(user_id) is None:
            raise NotFoundError(f"Usuario {user_id} no encontrado")
        if await self._members.get(list_id, user_id) is not None:
            raise ConflictError(f"El usuario {user_id} ya es miembro de la lista {list_id}")
        return await self._members.add(list_id=list_id, user_id=user_id, role=role)

    async def set_member_role(
        self, list_id: int, requester_id: int, *, user_id: int, role: ListRole
    ) -> ListMember:
        await self._require_role(list_id, requester_id, ListRole.OWNER)
        await self._guard_original_owner(list_id, user_id)
        updated = await self._members.set_role(list_id, user_id, role)
        if updated is None:
            raise NotFoundError(f"El usuario {user_id} no es miembro de la lista {list_id}")
        return updated

    async def remove_member(self, list_id: int, requester_id: int, *, user_id: int) -> None:
        await self._require_role(list_id, requester_id, ListRole.OWNER)
        await self._guard_original_owner(list_id, user_id)
        removed = await self._members.remove(list_id, user_id)
        if not removed:
            raise NotFoundError(f"El usuario {user_id} no es miembro de la lista {list_id}")
        # Preserva la invariante de ADR-20: el responsable de una tarea debe ser miembro de la
        # lista. Al expulsar a un colaborador se desasignan (assignee_id → NULL) sus tareas en
        # la **misma unidad de trabajo** que la expulsión, de forma análoga al
        # `ON DELETE SET NULL` del FK cuando se elimina el usuario. Así no queda un no-miembro
        # como responsable ni se reenvían invitaciones a quien ya no puede ver la lista.
        await self._tasks.clear_assignee_in_list(list_id, user_id)

    async def _guard_original_owner(self, list_id: int, user_id: int) -> None:
        """Evita degradar o expulsar al creador, para no dejar la lista sin dueño."""
        task_list = await self._repository.get(list_id)
        if task_list is not None and task_list.owner_id == user_id:
            raise ForbiddenError("No se puede modificar al propietario original de la lista")
