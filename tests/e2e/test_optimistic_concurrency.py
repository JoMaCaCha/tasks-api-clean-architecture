"""E2E de concurrencia optimista en tareas (ADR-26) contra el servidor real + PostgreSQL.

Valida lo que la suite de integración NO puede: la suite en proceso corre sobre SQLite con
`StaticPool` (una única conexión), así que es incapaz de ejercer dos transacciones en
paralelo y el camino `version_id_col → StaleDataError → 412` nunca se ejecuta ahí. Aquí,
sobre PostgreSQL real y dos peticiones concurrentes, se comprueba que ante un *lost update*
exactamente una escritura gana (200) y la otra se rechaza (412).

También cubre el contrato HTTP de la precondición: emisión del `ETag`, 428 sin `If-Match`,
412 con ETag obsoleto, recuperación releyendo, y el comodín `If-Match: *`.
"""

import asyncio

import httpx
import pytest

from tests.e2e.helpers import etag, new_user

pytestmark = pytest.mark.e2e


async def _new_task(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> tuple[int, int, httpx.Response]:
    """Crea una lista y una tarea; devuelve `(list_id, task_id, respuesta_de_creación)`."""
    list_id = (await client.post("/api/v1/lists", json={"title": "OCC"}, headers=headers)).json()[
        "id"
    ]
    created = await client.post(
        f"/api/v1/lists/{list_id}/tasks", json={"title": "t"}, headers=headers
    )
    assert created.status_code == 201
    return list_id, created.json()["id"], created


async def test_etag_emitted_and_matches_version(client: httpx.AsyncClient) -> None:
    _, headers = await new_user(client, "occ-etag")
    list_id, task_id, created = await _new_task(client, headers)
    # La creación expone el ETag de la versión inicial y el campo `version`.
    assert created.headers["etag"] == '"1"'
    assert created.json()["version"] == 1
    got = await client.get(f"/api/v1/lists/{list_id}/tasks/{task_id}", headers=headers)
    assert got.headers["etag"] == '"1"' and got.json()["version"] == 1


async def test_missing_if_match_returns_428(client: httpx.AsyncClient) -> None:
    _, headers = await new_user(client, "occ-428")
    list_id, task_id, _ = await _new_task(client, headers)
    resp = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/status",
        json={"status": "done"},
        headers=headers,  # sin If-Match
    )
    assert resp.status_code == 428


async def test_stale_etag_returns_412_then_recovers(client: httpx.AsyncClient) -> None:
    _, headers = await new_user(client, "occ-412")
    list_id, task_id, created = await _new_task(client, headers)
    stale = etag(created)  # "1"

    first = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/status",
        json={"status": "in_progress"},
        headers={**headers, "If-Match": stale},
    )
    assert first.status_code == 200 and first.headers["etag"] == '"2"'

    conflict = await client.put(
        f"/api/v1/lists/{list_id}/tasks/{task_id}",
        json={"title": "pisotón", "status": "done", "priority": "low"},
        headers={**headers, "If-Match": stale},  # ETag ya obsoleto
    )
    assert conflict.status_code == 412

    fresh = await client.get(f"/api/v1/lists/{list_id}/tasks/{task_id}", headers=headers)
    ok = await client.put(
        f"/api/v1/lists/{list_id}/tasks/{task_id}",
        json={"title": "ok", "status": "done", "priority": "low"},
        headers={**headers, "If-Match": etag(fresh)},
    )
    assert ok.status_code == 200


async def test_wildcard_if_match_accepted(client: httpx.AsyncClient) -> None:
    _, headers = await new_user(client, "occ-wild")
    list_id, task_id, _ = await _new_task(client, headers)
    resp = await client.patch(
        f"/api/v1/lists/{list_id}/tasks/{task_id}/status",
        json={"status": "done"},
        headers={**headers, "If-Match": "*"},
    )
    assert resp.status_code == 200


async def test_concurrent_writes_one_wins_one_412(client: httpx.AsyncClient) -> None:
    """Dos escrituras concurrentes con el MISMO ETag: una gana (200), la otra pierde (412).

    Es el corazón de ADR-26 y solo es verificable sobre PostgreSQL real: el bloqueo de fila
    serializa los dos UPDATE y `version_id_col` hace que el segundo no encuentre la versión
    esperada (`StaleDataError` → 412). Garantiza que no hay *lost update* bajo concurrencia.
    """
    _, headers = await new_user(client, "occ-race")
    list_id, task_id, created = await _new_task(client, headers)
    same_etag = etag(created)  # ambos clientes parten de "1"

    async def patch(status: str) -> int:
        resp = await client.patch(
            f"/api/v1/lists/{list_id}/tasks/{task_id}/status",
            json={"status": status},
            headers={**headers, "If-Match": same_etag},
        )
        return resp.status_code

    codes = await asyncio.gather(patch("in_progress"), patch("done"))
    assert sorted(codes) == [200, 412], f"esperado un ganador y un conflicto, fue {codes}"
