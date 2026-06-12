"""E2E de CRUD completo, paginación, filtros y colaboradores/RBAC contra el servidor real.

Cierra los huecos del smoke: actualización y obtención de listas y tareas, borrado de
tareas, filtros por estado y prioridad, paginación por keyset (`next_cursor`) y el modelo
de colaboradores con roles (ADR-14: viewer/editor/owner, 403 por rol insuficiente, 404 a
no-miembros). Todo sobre HTTP real + PostgreSQL real.
"""

import httpx
import pytest

from tests.e2e.helpers import etag, new_user

pytestmark = pytest.mark.e2e


# --- CRUD de listas ---------------------------------------------------------


async def test_list_update_and_detail_completion(client: httpx.AsyncClient) -> None:
    _, headers = await new_user(client, "listcrud")
    created = await client.post(
        "/api/v1/lists", json={"title": "Original", "description": "d0"}, headers=headers
    )
    list_id = created.json()["id"]

    updated = await client.put(
        f"/api/v1/lists/{list_id}",
        json={"title": "Renombrada", "description": "d1"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Renombrada"

    # Detalle de la lista con % de completitud (sin tareas → 0.0).
    detail = await client.get(f"/api/v1/lists/{list_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["completion_percentage"] == 0.0
    assert detail.json()["title"] == "Renombrada"


async def test_lists_keyset_pagination(client: httpx.AsyncClient) -> None:
    _, headers = await new_user(client, "listpage")  # usuario nuevo: solo ve sus listas
    for i in range(3):
        await client.post("/api/v1/lists", json={"title": f"L{i}"}, headers=headers)

    page1 = await client.get("/api/v1/lists?limit=2", headers=headers)
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["items"]) == 2
    assert body1["next_cursor"] is not None

    page2 = await client.get(
        f"/api/v1/lists?limit=2&cursor={body1['next_cursor']}", headers=headers
    )
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["next_cursor"] is None


# --- CRUD de tareas ---------------------------------------------------------


async def test_task_get_update_delete(client: httpx.AsyncClient) -> None:
    _, headers = await new_user(client, "taskcrud")
    list_id = (await client.post("/api/v1/lists", json={"title": "T"}, headers=headers)).json()[
        "id"
    ]
    task_id = (
        await client.post(
            f"/api/v1/lists/{list_id}/tasks",
            json={"title": "tarea", "priority": "low"},
            headers=headers,
        )
    ).json()["id"]

    got = await client.get(f"/api/v1/lists/{list_id}/tasks/{task_id}", headers=headers)
    assert got.status_code == 200 and got.json()["title"] == "tarea"

    # Escrituras condicionales: cada mutación manda el ETag vigente en `If-Match` (ADR-26).
    updated = await client.put(
        f"/api/v1/lists/{list_id}/tasks/{task_id}",
        json={"title": "tarea v2", "description": "d", "status": "in_progress", "priority": "high"},
        headers={**headers, "If-Match": got.headers["etag"]},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "in_progress" and updated.json()["priority"] == "high"

    deleted = await client.delete(
        f"/api/v1/lists/{list_id}/tasks/{task_id}",
        headers={**headers, "If-Match": updated.headers["etag"]},
    )
    assert deleted.status_code == 204
    gone = await client.get(f"/api/v1/lists/{list_id}/tasks/{task_id}", headers=headers)
    assert gone.status_code == 404


async def test_assign_non_member_returns_409_then_ok(client: httpx.AsyncClient) -> None:
    # ADR-20 sobre el servidor real: asignar a un usuario que existe pero no es colaborador
    # devuelve 409; tras añadirlo como viewer, la asignación procede. La escritura es
    # condicional (ADR-26): un 409 no muta la tarea, así que el ETag inicial sigue vigente.
    owner_id, owner = await new_user(client, "assign-owner")
    outsider_id, _ = await new_user(client, "assign-outsider")
    list_id = (
        await client.post("/api/v1/lists", json={"title": "Asignación"}, headers=owner)
    ).json()["id"]
    created = await client.post(
        f"/api/v1/lists/{list_id}/tasks", json={"title": "t"}, headers=owner
    )
    task_id = created.json()["id"]
    tag = etag(created)  # "1": ni el 409 ni el 404 suben la versión

    blocked = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/assignee",
        json={"assignee_id": outsider_id},
        headers={**owner, "If-Match": tag},
    )
    assert blocked.status_code == 409

    await client.post(
        f"/api/v1/lists/{list_id}/members",
        json={"user_id": outsider_id, "role": "viewer"},
        headers=owner,
    )
    ok = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/assignee",
        json={"assignee_id": outsider_id},
        headers={**owner, "If-Match": tag},  # sigue siendo "1"
    )
    assert ok.status_code == 200 and ok.json()["assignee_id"] == outsider_id


async def test_tasks_filters_and_pagination(client: httpx.AsyncClient) -> None:
    _, headers = await new_user(client, "taskfilter")
    list_id = (await client.post("/api/v1/lists", json={"title": "F"}, headers=headers)).json()[
        "id"
    ]
    await client.post(
        f"/api/v1/lists/{list_id}/tasks",
        json={"title": "a", "status": "done", "priority": "high"},
        headers=headers,
    )
    await client.post(
        f"/api/v1/lists/{list_id}/tasks",
        json={"title": "b", "status": "pending", "priority": "low"},
        headers=headers,
    )
    await client.post(
        f"/api/v1/lists/{list_id}/tasks",
        json={"title": "c", "status": "pending", "priority": "high"},
        headers=headers,
    )

    by_status = await client.get(f"/api/v1/lists/{list_id}/tasks?status=done", headers=headers)
    assert [t["title"] for t in by_status.json()["tasks"]] == ["a"]

    by_priority = await client.get(f"/api/v1/lists/{list_id}/tasks?priority=high", headers=headers)
    assert {t["title"] for t in by_priority.json()["tasks"]} == {"a", "c"}

    # % de completitud sobre TODAS las tareas (1 de 3 done ≈ 33.33), no sobre el filtro.
    full = await client.get(f"/api/v1/lists/{list_id}/tasks", headers=headers)
    assert full.json()["completion_percentage"] == pytest.approx(33.33)

    # Paginación por keyset sobre las tareas.
    page1 = await client.get(f"/api/v1/lists/{list_id}/tasks?limit=2", headers=headers)
    assert len(page1.json()["tasks"]) == 2 and page1.json()["next_cursor"] is not None
    cursor = page1.json()["next_cursor"]
    page2 = await client.get(
        f"/api/v1/lists/{list_id}/tasks?limit=2&cursor={cursor}", headers=headers
    )
    assert len(page2.json()["tasks"]) == 1 and page2.json()["next_cursor"] is None


# --- Colaboradores y RBAC (ADR-14) ------------------------------------------


async def test_collaborator_roles_and_access_control(client: httpx.AsyncClient) -> None:
    owner_id, owner = await new_user(client, "owner")
    collaborator_id, collaborator = await new_user(client, "collab")
    _, outsider = await new_user(client, "outsider")

    list_id = (
        await client.post("/api/v1/lists", json={"title": "Compartida"}, headers=owner)
    ).json()["id"]

    # Un no-miembro no puede ni ver la lista: 404 (no se filtra su existencia).
    assert (await client.get(f"/api/v1/lists/{list_id}", headers=outsider)).status_code == 404

    # El owner añade al colaborador como viewer.
    add = await client.post(
        f"/api/v1/lists/{list_id}/members",
        json={"user_id": collaborator_id, "role": "viewer"},
        headers=owner,
    )
    assert add.status_code == 201

    # Viewer puede leer, pero no escribir tareas (403).
    assert (await client.get(f"/api/v1/lists/{list_id}", headers=collaborator)).status_code == 200
    forbidden = await client.post(
        f"/api/v1/lists/{list_id}/tasks", json={"title": "x"}, headers=collaborator
    )
    assert forbidden.status_code == 403

    # El owner lo asciende a editor; ahora sí puede crear tareas.
    promote = await client.put(
        f"/api/v1/lists/{list_id}/members/{collaborator_id}",
        json={"role": "editor"},
        headers=owner,
    )
    assert promote.status_code == 200
    allowed = await client.post(
        f"/api/v1/lists/{list_id}/tasks", json={"title": "ok"}, headers=collaborator
    )
    assert allowed.status_code == 201

    # Listado de miembros: owner + colaborador.
    members = await client.get(f"/api/v1/lists/{list_id}/members", headers=owner)
    assert {m["user_id"] for m in members.json()["members"]} == {owner_id, collaborator_id}

    # No se puede degradar/expulsar al owner original (no dejar la lista huérfana).
    assert (
        await client.put(
            f"/api/v1/lists/{list_id}/members/{owner_id}",
            json={"role": "viewer"},
            headers=owner,
        )
    ).status_code == 403

    # El owner elimina al colaborador → pierde el acceso (404).
    assert (
        await client.delete(f"/api/v1/lists/{list_id}/members/{collaborator_id}", headers=owner)
    ).status_code == 204
    assert (await client.get(f"/api/v1/lists/{list_id}", headers=collaborator)).status_code == 404


async def test_non_owner_cannot_delete_list(client: httpx.AsyncClient) -> None:
    _, owner = await new_user(client, "owner2")
    editor_id, editor = await new_user(client, "editor2")
    list_id = (
        await client.post("/api/v1/lists", json={"title": "Solo-owner-borra"}, headers=owner)
    ).json()["id"]
    await client.post(
        f"/api/v1/lists/{list_id}/members",
        json={"user_id": editor_id, "role": "editor"},
        headers=owner,
    )
    # Un editor puede escribir tareas pero NO borrar la lista (requiere owner).
    assert (await client.delete(f"/api/v1/lists/{list_id}", headers=editor)).status_code == 403
    assert (await client.delete(f"/api/v1/lists/{list_id}", headers=owner)).status_code == 204
