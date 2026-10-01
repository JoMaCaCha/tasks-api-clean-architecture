"""Smoke E2E contra el software realmente desplegado (HTTP real + PostgreSQL real).

Valida lo que el suite en proceso no puede: el servidor uvicorn de verdad, el arranque
vía `lifespan` (creación del esquema), la conexión real a PostgreSQL y el flujo completo
de negocio de extremo a extremo. Opt-in: ejecutar con `pytest -m e2e --no-cov` con el
servidor levantado (`docker compose up`).
"""

from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.e2e


async def test_health_and_readiness(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}
    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}  # readiness real contra PostgreSQL


async def test_protected_endpoint_requires_auth(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/lists")).status_code == 401


async def test_full_task_flow(client: httpx.AsyncClient) -> None:
    # Email único: el PostgreSQL real es persistente entre corridas.
    creds = {"email": f"e2e-{uuid4().hex}@example.com", "password": "supersecret123"}

    assert (await client.post("/api/v1/auth/register", json=creds)).status_code == 201
    login = await client.post("/api/v1/auth/login", json=creds)
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    created_list = await client.post("/api/v1/lists", json={"title": "E2E"}, headers=headers)
    assert created_list.status_code == 201
    list_id = created_list.json()["id"]

    first = await client.post(
        f"/api/v1/lists/{list_id}/tasks",
        json={"title": "a", "priority": "high"},
        headers=headers,
    )
    assert first.status_code == 201
    task_id = first.json()["id"]
    await client.post(
        f"/api/v1/lists/{list_id}/tasks",
        json={"title": "b", "status": "done"},
        headers=headers,
    )

    patched = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/status",
        json={"status": "done"},
        headers={**headers, "If-Match": first.headers["etag"]},
    )
    assert patched.status_code == 200

    listing = await client.get(f"/api/v1/lists/{list_id}/tasks", headers=headers)
    assert listing.status_code == 200
    body = listing.json()
    assert len(body["tasks"]) == 2
    assert body["completion_percentage"] == 100.0  # ambas tareas en estado done

    filtered = await client.get(f"/api/v1/lists/{list_id}/tasks?priority=high", headers=headers)
    assert len(filtered.json()["tasks"]) == 1

    # Asignar un responsable real (dispara la notificación simulada en el servidor). El
    # responsable debe ser miembro de la lista (ADR-20), así que primero se añade como
    # colaborador y luego se asigna.
    assignee = await client.post(
        "/api/v1/auth/register",
        json={"email": f"assignee-{uuid4().hex}@example.com", "password": "supersecret123"},
    )
    assignee_id = assignee.json()["id"]
    add_member = await client.post(
        f"/api/v1/lists/{list_id}/members",
        json={"user_id": assignee_id, "role": "viewer"},
        headers=headers,
    )
    assert add_member.status_code == 201
    assigned = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/assignee",
        json={"assignee_id": assignee_id},
        # La tarea cambió de versión al marcarla `done`; se usa el ETag de esa respuesta.
        headers={**headers, "If-Match": patched.headers["etag"]},
    )
    assert assigned.status_code == 200
    assert assigned.json()["assignee_id"] == assignee_id

    # Limpieza: borra la lista (cascada a sus tareas) para no dejar residuos.
    assert (await client.delete(f"/api/v1/lists/{list_id}", headers=headers)).status_code == 204
