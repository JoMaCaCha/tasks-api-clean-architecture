"""Endpoints de listas de tareas y de colaboradores.

Protegidos con JWT y autorizados por rol de pertenencia. Los listados usan paginación
por keyset (`cursor` = id del último elemento de la página previa).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.application.task_list_service import TaskListService
from app.web.dependencies import (
    CurrentUserDep,
    get_current_user,
    get_task_list_service,
)
from app.web.schemas import (
    AddMemberRequest,
    ListMemberPage,
    ListMemberResponse,
    TaskListCreate,
    TaskListDetailResponse,
    TaskListPage,
    TaskListResponse,
    TaskListUpdate,
    UpdateMemberRoleRequest,
)

router = APIRouter(
    prefix="/lists",
    tags=["lists"],
    dependencies=[Depends(get_current_user)],
)

ListServiceDep = Annotated[TaskListService, Depends(get_task_list_service)]


@router.post("", response_model=TaskListResponse, status_code=status.HTTP_201_CREATED)
async def create_list(
    body: TaskListCreate, service: ListServiceDep, user: CurrentUserDep
) -> TaskListResponse:
    task_list = await service.create(
        owner_id=user.id, title=body.title, description=body.description
    )
    return TaskListResponse.model_validate(task_list)


@router.get("", response_model=TaskListPage)
async def list_lists(
    service: ListServiceDep,
    user: CurrentUserDep,
    limit: int = Query(50, ge=1, le=100),
    cursor: int | None = Query(None, ge=0, description="id del último elemento ya visto"),
) -> TaskListPage:
    # Se pide un elemento de más (`limit + 1`) para saber con certeza si hay página
    # siguiente sin un COUNT extra; se devuelven solo `limit` y el sobrante solo indica
    # "hay más". Evita emitir un cursor que llevaría a una página final vacía.
    fetched = await service.list_all(user.id, limit=limit + 1, after_id=cursor)
    has_more = len(fetched) > limit
    items = fetched[:limit]
    next_cursor = items[-1].id if has_more else None
    return TaskListPage(
        items=[TaskListResponse.model_validate(item) for item in items],
        limit=limit,
        next_cursor=next_cursor,
    )


@router.get("/{list_id}", response_model=TaskListDetailResponse)
async def get_list(
    list_id: int,
    service: ListServiceDep,
    user: CurrentUserDep,
) -> TaskListDetailResponse:
    # Una sola autorización para la lista y su % de completitud (evita repetir el chequeo
    # de pertenencia que antes hacían `get` y `completion_percentage` por separado).
    task_list, completion = await service.get_with_completion(list_id, user.id)
    return TaskListDetailResponse(**task_list.model_dump(), completion_percentage=completion)


@router.put("/{list_id}", response_model=TaskListResponse)
async def update_list(
    list_id: int, body: TaskListUpdate, service: ListServiceDep, user: CurrentUserDep
) -> TaskListResponse:
    task_list = await service.update(
        list_id, user.id, title=body.title, description=body.description
    )
    return TaskListResponse.model_validate(task_list)


@router.delete("/{list_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_list(list_id: int, service: ListServiceDep, user: CurrentUserDep) -> None:
    await service.delete(list_id, user.id)


# --- Colaboradores -----------------------------------------------------------


@router.get("/{list_id}/members", response_model=ListMemberPage)
async def list_members(
    list_id: int,
    service: ListServiceDep,
    user: CurrentUserDep,
    limit: int = Query(50, ge=1, le=100),
    cursor: int | None = Query(None, ge=0, description="user_id del último miembro ya visto"),
) -> ListMemberPage:
    # Mismo patrón keyset que listas y tareas (`limit + 1` para detectar página siguiente
    # sin COUNT). El cursor es el `user_id` del último miembro devuelto.
    fetched = await service.list_members(list_id, user.id, limit=limit + 1, after_user_id=cursor)
    has_more = len(fetched) > limit
    members = fetched[:limit]
    next_cursor = members[-1].user_id if has_more else None
    return ListMemberPage(
        members=[ListMemberResponse.model_validate(m) for m in members],
        limit=limit,
        next_cursor=next_cursor,
    )


@router.post(
    "/{list_id}/members",
    response_model=ListMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    list_id: int, body: AddMemberRequest, service: ListServiceDep, user: CurrentUserDep
) -> ListMemberResponse:
    member = await service.add_member(list_id, user.id, user_id=body.user_id, role=body.role)
    return ListMemberResponse.model_validate(member)


# El segmento de ruta identifica al miembro por su `user_id` (la membresía tiene clave
# compuesta `(list_id, user_id)`, sin id propio). Se nombra `user_id` —no `member_id`— para
# que coincida con el cuerpo (`AddMemberRequest.user_id`), con el parámetro del servicio y
# con el contrato publicado en el README.
@router.put("/{list_id}/members/{user_id}", response_model=ListMemberResponse)
async def update_member_role(
    list_id: int,
    user_id: int,
    body: UpdateMemberRoleRequest,
    service: ListServiceDep,
    user: CurrentUserDep,
) -> ListMemberResponse:
    member = await service.set_member_role(list_id, user.id, user_id=user_id, role=body.role)
    return ListMemberResponse.model_validate(member)


@router.delete("/{list_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    list_id: int, user_id: int, service: ListServiceDep, user: CurrentUserDep
) -> None:
    await service.remove_member(list_id, user.id, user_id=user_id)
