"""Tests de integración de tareas: CRUD, filtros, estado, completitud y asignación."""

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.outbox_service import OutboxProcessor
from app.domain.notifications import INotifier
from app.infrastructure.db.repositories import SqlAlchemyOutboxRepository


class _SpyNotifier(INotifier):
    """Notificador espía: registra cada invocación en memoria."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def notify_task_assignment(self, *, email: str, task_title: str) -> None:
        self.calls.append((email, task_title))


async def _drain_outbox(
    session_factory: async_sessionmaker[AsyncSession], notifier: INotifier
) -> int:
    """Ejecuta el procesador del outbox una vez (el worker no corre en los tests)."""
    async with session_factory() as session:
        processor = OutboxProcessor(
            outbox_repository=SqlAlchemyOutboxRepository(session), notifier=notifier
        )
        count = await processor.process_pending()
        # El procesador hace flush; el commit lo controla la unidad de trabajo (aquí, el
        # helper), igual que el worker en producción (process_outbox_once).
        await session.commit()
        return count


@pytest_asyncio.fixture
async def list_id(client: AsyncClient, auth_headers: dict[str, str]) -> int:
    response = await client.post("/api/v1/lists", json={"title": "Tareas"}, headers=auth_headers)
    return response.json()["id"]


def _if_match(headers: dict[str, str], etag: str) -> dict[str, str]:
    """Cabeceras de autenticación + precondición `If-Match` para una escritura condicional."""
    return {**headers, "If-Match": etag}


async def test_task_crud_and_status(
    client: AsyncClient, auth_headers: dict[str, str], list_id: int
) -> None:
    created = await client.post(
        f"/api/v1/lists/{list_id}/tasks",
        json={"title": "Diseñar", "priority": "high"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert created.json()["status"] == "pending"
    assert created.json()["version"] == 1
    # La creación devuelve el ETag de la versión inicial.
    assert created.headers["etag"] == '"1"'

    fetched = await client.get(f"/api/v1/lists/{list_id}/tasks/{task_id}", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.headers["etag"] == '"1"'

    # Cada mutación exige el ETag vigente (If-Match) y devuelve el nuevo.
    patched = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/status",
        json={"status": "done"},
        headers=_if_match(auth_headers, fetched.headers["etag"]),
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "done"
    assert patched.headers["etag"] == '"2"'

    updated = await client.put(
        f"/api/v1/lists/{list_id}/tasks/{task_id}",
        json={"title": "Diseñar v2", "status": "in_progress", "priority": "low"},
        headers=_if_match(auth_headers, patched.headers["etag"]),
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Diseñar v2"
    assert updated.headers["etag"] == '"3"'

    deleted = await client.delete(
        f"/api/v1/lists/{list_id}/tasks/{task_id}",
        headers=_if_match(auth_headers, updated.headers["etag"]),
    )
    assert deleted.status_code == 204


async def test_mutation_without_if_match_returns_428(
    client: AsyncClient, auth_headers: dict[str, str], list_id: int
) -> None:
    """Sin `If-Match`, una escritura condicional se rechaza con 428 Precondition Required."""
    created = await client.post(
        f"/api/v1/lists/{list_id}/tasks", json={"title": "t"}, headers=auth_headers
    )
    task_id = created.json()["id"]
    resp = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/status",
        json={"status": "done"},
        headers=auth_headers,
    )
    assert resp.status_code == 428


async def test_stale_if_match_returns_412(
    client: AsyncClient, auth_headers: dict[str, str], list_id: int
) -> None:
    """Un `If-Match` que quedó obsoleto (otra escritura subió la versión) → 412."""
    created = await client.post(
        f"/api/v1/lists/{list_id}/tasks", json={"title": "t"}, headers=auth_headers
    )
    task_id = created.json()["id"]
    stale_etag = created.headers["etag"]  # "1"

    first = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/status",
        json={"status": "in_progress"},
        headers=_if_match(auth_headers, stale_etag),
    )
    assert first.status_code == 200  # versión ahora "2"

    conflict = await client.put(
        f"/api/v1/lists/{list_id}/tasks/{task_id}",
        json={"title": "pisotón", "status": "done", "priority": "low"},
        headers=_if_match(auth_headers, stale_etag),  # reusa el ETag viejo
    )
    assert conflict.status_code == 412

    # Releyendo el ETag actual, la escritura procede.
    current = await client.get(f"/api/v1/lists/{list_id}/tasks/{task_id}", headers=auth_headers)
    ok = await client.put(
        f"/api/v1/lists/{list_id}/tasks/{task_id}",
        json={"title": "ok", "status": "done", "priority": "low"},
        headers=_if_match(auth_headers, current.headers["etag"]),
    )
    assert ok.status_code == 200


async def test_wildcard_if_match_is_accepted(
    client: AsyncClient, auth_headers: dict[str, str], list_id: int
) -> None:
    """`If-Match: *` procede con cualquier versión vigente de la tarea."""
    created = await client.post(
        f"/api/v1/lists/{list_id}/tasks", json={"title": "t"}, headers=auth_headers
    )
    task_id = created.json()["id"]
    resp = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/status",
        json={"status": "done"},
        headers=_if_match(auth_headers, "*"),
    )
    assert resp.status_code == 200


async def test_list_tasks_filters_and_completion(
    client: AsyncClient, auth_headers: dict[str, str], list_id: int
) -> None:
    await client.post(
        f"/api/v1/lists/{list_id}/tasks",
        json={"title": "a", "status": "done", "priority": "high"},
        headers=auth_headers,
    )
    await client.post(
        f"/api/v1/lists/{list_id}/tasks",
        json={"title": "b", "status": "pending", "priority": "low"},
        headers=auth_headers,
    )

    all_tasks = await client.get(f"/api/v1/lists/{list_id}/tasks", headers=auth_headers)
    assert all_tasks.status_code == 200
    body = all_tasks.json()
    assert len(body["tasks"]) == 2
    assert body["completion_percentage"] == 50.0
    assert body["limit"] == 50 and body["next_cursor"] is None

    filtered = await client.get(f"/api/v1/lists/{list_id}/tasks?status=done", headers=auth_headers)
    assert len(filtered.json()["tasks"]) == 1

    by_priority = await client.get(
        f"/api/v1/lists/{list_id}/tasks?priority=low", headers=auth_headers
    )
    assert len(by_priority.json()["tasks"]) == 1


async def test_tasks_full_page_has_null_cursor(
    client: AsyncClient, auth_headers: dict[str, str], list_id: int
) -> None:
    # Total == limit en el listado de tareas: viene lleno pero sin más → cursor null.
    for i in range(2):
        await client.post(
            f"/api/v1/lists/{list_id}/tasks", json={"title": f"t{i}"}, headers=auth_headers
        )
    body = (await client.get(f"/api/v1/lists/{list_id}/tasks?limit=2", headers=auth_headers)).json()
    assert len(body["tasks"]) == 2
    assert body["next_cursor"] is None


async def test_create_task_in_unknown_list_returns_404(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/lists/9999/tasks", json={"title": "x"}, headers=auth_headers
    )
    assert response.status_code == 404


async def test_assign_task_to_user(
    client: AsyncClient, auth_headers: dict[str, str], list_id: int
) -> None:
    # El usuario autenticado (id=1) sirve como responsable.
    task = await client.post(
        f"/api/v1/lists/{list_id}/tasks", json={"title": "t"}, headers=auth_headers
    )
    task_id = task.json()["id"]

    assigned = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/assignee",
        json={"assignee_id": 1},
        headers=_if_match(auth_headers, task.headers["etag"]),
    )
    assert assigned.status_code == 200
    assert assigned.json()["assignee_id"] == 1


async def test_assign_non_member_returns_409_then_ok_after_adding(
    client: AsyncClient, auth_headers: dict[str, str], list_id: int
) -> None:
    # ADR-20: el responsable debe ser miembro de la lista. Asignar a un usuario que existe
    # pero no es colaborador devuelve 409 (no una invitación a quien no puede ver la lista);
    # tras añadirlo como viewer, la asignación procede.
    outsider = await client.post(
        "/api/v1/auth/register",
        json={"email": "outsider@crehana.com", "password": "supersecret123"},
    )
    outsider_id = outsider.json()["id"]
    task = await client.post(
        f"/api/v1/lists/{list_id}/tasks", json={"title": "t"}, headers=auth_headers
    )
    task_id = task.json()["id"]
    etag = task.headers["etag"]  # un 409/404 no muta la tarea: la versión no cambia

    blocked = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/assignee",
        json={"assignee_id": outsider_id},
        headers=_if_match(auth_headers, etag),
    )
    assert blocked.status_code == 409

    await client.post(
        f"/api/v1/lists/{list_id}/members",
        json={"user_id": outsider_id, "role": "viewer"},
        headers=auth_headers,
    )
    ok = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/assignee",
        json={"assignee_id": outsider_id},
        headers=_if_match(auth_headers, etag),  # sigue siendo "1": el 409 no subió la versión
    )
    assert ok.status_code == 200 and ok.json()["assignee_id"] == outsider_id


async def test_assign_unknown_user_returns_404(
    client: AsyncClient, auth_headers: dict[str, str], list_id: int
) -> None:
    task = await client.post(
        f"/api/v1/lists/{list_id}/tasks", json={"title": "t"}, headers=auth_headers
    )
    task_id = task.json()["id"]
    response = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/assignee",
        json={"assignee_id": 9999},
        headers=_if_match(auth_headers, task.headers["etag"]),
    )
    assert response.status_code == 404


async def test_assign_enqueues_invitation_delivered_by_outbox(
    client: AsyncClient,
    auth_headers: dict[str, str],
    list_id: int,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Asignar encola la invitación en el outbox; el procesador la entrega (idempotente).
    task = await client.post(
        f"/api/v1/lists/{list_id}/tasks", json={"title": "Documentar"}, headers=auth_headers
    )
    task_id = task.json()["id"]

    response = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/assignee",
        json={"assignee_id": 1},  # el usuario autenticado (tester@crehana.com) es id=1
        headers=_if_match(auth_headers, task.headers["etag"]),
    )
    assert response.status_code == 200

    spy = _SpyNotifier()
    assert await _drain_outbox(session_factory, spy) == 1
    assert spy.calls == [("tester@crehana.com", "Documentar")]
    # Una segunda pasada no reenvía (ya entregado).
    assert await _drain_outbox(session_factory, spy) == 0


async def test_removing_member_unassigns_their_tasks(
    client: AsyncClient, auth_headers: dict[str, str], list_id: int
) -> None:
    # ADR-20: al expulsar a un colaborador, sus tareas en la lista quedan sin responsable
    # (assignee_id → NULL) en la misma transacción que la expulsión. Ejercita el UPDATE real.
    collab = await client.post(
        "/api/v1/auth/register",
        json={"email": "collab-unassign@crehana.com", "password": "supersecret123"},
    )
    collab_id = collab.json()["id"]
    await client.post(
        f"/api/v1/lists/{list_id}/members",
        json={"user_id": collab_id, "role": "editor"},
        headers=auth_headers,
    )
    created = await client.post(
        f"/api/v1/lists/{list_id}/tasks",
        json={"title": "t", "assignee_id": collab_id},
        headers=auth_headers,
    )
    assert created.status_code == 201 and created.json()["assignee_id"] == collab_id
    task_id = created.json()["id"]

    removed = await client.delete(
        f"/api/v1/lists/{list_id}/members/{collab_id}", headers=auth_headers
    )
    assert removed.status_code == 204

    fetched = await client.get(f"/api/v1/lists/{list_id}/tasks/{task_id}", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["assignee_id"] is None


async def test_create_with_assignee_enqueues_invitation(
    client: AsyncClient,
    auth_headers: dict[str, str],
    list_id: int,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # La invitación también se encola al CREAR una tarea ya asignada (ver ADR-9/ADR-15).
    response = await client.post(
        f"/api/v1/lists/{list_id}/tasks",
        json={"title": "Revisar", "assignee_id": 1},
        headers=auth_headers,
    )
    assert response.status_code == 201

    spy = _SpyNotifier()
    assert await _drain_outbox(session_factory, spy) == 1
    assert spy.calls == [("tester@crehana.com", "Revisar")]
