"""Endpoints de tareas dentro de una lista (§1.a.ii–iv) y bonus (§1.b.iii–iv).

Protegidos con JWT y autorizados por rol de pertenencia. Cuando se fija un responsable
nuevo (al crear, actualizar o asignar) se **encola** una invitación en el outbox de
notificaciones (entrega fiable con reintentos por un worker; ver DECISION_LOG ADR-15).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.application.task_service import TaskService
from app.domain.entities import Task, User
from app.domain.enums import TaskPriority, TaskStatus
from app.domain.repositories import IOutboxRepository
from app.web.dependencies import (
    CurrentUserDep,
    get_current_user,
    get_outbox_repository,
    get_task_service,
)
from app.web.etag import make_etag, require_if_match
from app.web.schemas import (
    TaskAssigneeUpdate,
    TaskCollectionResponse,
    TaskCreate,
    TaskResponse,
    TaskStatusUpdate,
    TaskUpdate,
)

router = APIRouter(
    prefix="/lists/{list_id}/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_current_user)],
)

TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]
OutboxDep = Annotated[IOutboxRepository, Depends(get_outbox_repository)]

# Cabecera `If-Match` obligatoria en las mutaciones de una tarea existente (concurrencia
# optimista). Se documenta en OpenAPI; su ausencia la rechaza `require_if_match` con 428.
IfMatchHeader = Annotated[
    str | None,
    Header(description="ETag de la última lectura de la tarea (concurrencia optimista)."),
]


async def _enqueue_invitation(outbox: IOutboxRepository, assignee: User | None, task: Task) -> None:
    """Encola la invitación si hay un responsable nuevo. Idempotente por (tarea, usuario)."""
    if assignee is not None:
        await outbox.enqueue(
            idempotency_key=f"task_assignment:{task.id}:{assignee.id}",
            email=assignee.email,
            task_title=task.title,
        )


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    list_id: int,
    body: TaskCreate,
    service: TaskServiceDep,
    user: CurrentUserDep,
    outbox: OutboxDep,
    response: Response,
) -> TaskResponse:
    task, assignee = await service.create(
        user_id=user.id,
        list_id=list_id,
        title=body.title,
        description=body.description,
        status=body.status,
        priority=body.priority,
        assignee_id=body.assignee_id,
    )
    await _enqueue_invitation(outbox, assignee, task)
    # ETag de la tarea recién creada: el cliente lo usa como `If-Match` en la primera escritura.
    response.headers["ETag"] = make_etag(task.version)
    return TaskResponse.model_validate(task)


@router.get("", response_model=TaskCollectionResponse)
async def list_tasks(
    list_id: int,
    service: TaskServiceDep,
    user: CurrentUserDep,
    status_filter: Annotated[TaskStatus | None, Query(alias="status")] = None,
    priority: TaskPriority | None = None,
    limit: int = Query(50, ge=1, le=100),
    cursor: int | None = Query(None, ge=0, description="id de la última tarea ya vista"),
) -> TaskCollectionResponse:
    # `limit + 1`: el elemento sobrante solo sirve para detectar si hay página siguiente
    # (sin COUNT) y no se devuelve. Evita un cursor que apuntaría a una página vacía.
    # Una sola autorización para tareas y % de completitud (antes se chequeaba el rol dos
    # veces, una por cada llamada).
    fetched, completion = await service.list_tasks_with_completion(
        list_id, user.id, status=status_filter, priority=priority, limit=limit + 1, after_id=cursor
    )
    has_more = len(fetched) > limit
    tasks = fetched[:limit]
    next_cursor = tasks[-1].id if has_more else None
    return TaskCollectionResponse(
        list_id=list_id,
        completion_percentage=completion,
        limit=limit,
        next_cursor=next_cursor,
        tasks=[TaskResponse.model_validate(task) for task in tasks],
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    list_id: int,
    task_id: int,
    service: TaskServiceDep,
    user: CurrentUserDep,
    response: Response,
) -> TaskResponse:
    task = await service.get(list_id, task_id, user.id)
    # ETag para escrituras condicionales: el cliente lo reenvía en `If-Match` (ver ADR-26).
    response.headers["ETag"] = make_etag(task.version)
    return TaskResponse.model_validate(task)


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    list_id: int,
    task_id: int,
    body: TaskUpdate,
    service: TaskServiceDep,
    user: CurrentUserDep,
    outbox: OutboxDep,
    response: Response,
    if_match: IfMatchHeader = None,
) -> TaskResponse:
    # Escritura condicional: `If-Match` ausente → 428; obsoleto → 412 (ver ADR-26).
    expected_version = require_if_match(if_match)
    task, assignee = await service.update(
        list_id,
        task_id,
        user.id,
        title=body.title,
        description=body.description,
        status=body.status,
        priority=body.priority,
        assignee_id=body.assignee_id,
        expected_version=expected_version,
    )
    await _enqueue_invitation(outbox, assignee, task)
    response.headers["ETag"] = make_etag(task.version)
    return TaskResponse.model_validate(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    list_id: int,
    task_id: int,
    service: TaskServiceDep,
    user: CurrentUserDep,
    if_match: IfMatchHeader = None,
) -> None:
    # Borrado condicional: no se elimina una tarea que cambió respecto al ETag del cliente.
    expected_version = require_if_match(if_match)
    await service.delete(list_id, task_id, user.id, expected_version=expected_version)


@router.patch("/{task_id}/status", response_model=TaskResponse)
async def change_status(
    list_id: int,
    task_id: int,
    body: TaskStatusUpdate,
    service: TaskServiceDep,
    user: CurrentUserDep,
    response: Response,
    if_match: IfMatchHeader = None,
) -> TaskResponse:
    expected_version = require_if_match(if_match)
    task = await service.change_status(
        list_id, task_id, user.id, body.status, expected_version=expected_version
    )
    response.headers["ETag"] = make_etag(task.version)
    return TaskResponse.model_validate(task)


@router.patch("/{task_id}/assignee", response_model=TaskResponse)
async def assign_task(
    list_id: int,
    task_id: int,
    body: TaskAssigneeUpdate,
    service: TaskServiceDep,
    user: CurrentUserDep,
    outbox: OutboxDep,
    response: Response,
    if_match: IfMatchHeader = None,
) -> TaskResponse:
    expected_version = require_if_match(if_match)
    task, assignee = await service.assign(
        list_id, task_id, user.id, body.assignee_id, expected_version=expected_version
    )
    await _enqueue_invitation(outbox, assignee, task)
    response.headers["ETag"] = make_etag(task.version)
    return TaskResponse.model_validate(task)
