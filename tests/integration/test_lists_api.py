"""Tests de integración del CRUD de listas, colaboradores y paginación keyset."""

from httpx import AsyncClient


async def _register(client: AsyncClient, email: str) -> int:
    creds = {"email": email, "password": "supersecret123"}
    return (await client.post("/api/v1/auth/register", json=creds)).json()["id"]


async def _register_login(client: AsyncClient, email: str) -> dict[str, str]:
    creds = {"email": email, "password": "supersecret123"}
    await client.post("/api/v1/auth/register", json=creds)
    token = (await client.post("/api/v1/auth/login", json=creds)).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_list_crud_flow(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    created = await client.post("/api/v1/lists", json={"title": "Sprint"}, headers=auth_headers)
    assert created.status_code == 201
    list_id = created.json()["id"]

    listing = await client.get("/api/v1/lists", headers=auth_headers)
    assert listing.status_code == 200
    body = listing.json()
    assert len(body["items"]) == 1
    assert body["limit"] == 50 and body["next_cursor"] is None

    detail = await client.get(f"/api/v1/lists/{list_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["completion_percentage"] == 0.0

    updated = await client.put(
        f"/api/v1/lists/{list_id}",
        json={"title": "Sprint 2", "description": "Q3"},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Sprint 2"

    deleted = await client.delete(f"/api/v1/lists/{list_id}", headers=auth_headers)
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/lists/{list_id}", headers=auth_headers)).status_code == 404


async def test_get_unknown_list_returns_404(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    assert (await client.get("/api/v1/lists/9999", headers=auth_headers)).status_code == 404


async def test_create_list_empty_title_returns_422(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post("/api/v1/lists", json={"title": ""}, headers=auth_headers)
    assert response.status_code == 422


async def test_lists_are_isolated_per_user(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post("/api/v1/lists", json={"title": "De A"}, headers=auth_headers)
    list_id = created.json()["id"]

    headers_b = await _register_login(client, "userb@crehana.com")
    listing_b = await client.get("/api/v1/lists", headers=headers_b)
    assert listing_b.json()["items"] == []
    # Acceder a la lista ajena devuelve 404 (no se filtra su existencia).
    assert (await client.get(f"/api/v1/lists/{list_id}", headers=headers_b)).status_code == 404
    assert (await client.delete(f"/api/v1/lists/{list_id}", headers=headers_b)).status_code == 404


async def test_lists_keyset_pagination(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    ids = []
    for i in range(3):
        resp = await client.post("/api/v1/lists", json={"title": f"L{i}"}, headers=auth_headers)
        ids.append(resp.json()["id"])

    page1 = (await client.get("/api/v1/lists?limit=2", headers=auth_headers)).json()
    assert [it["id"] for it in page1["items"]] == ids[:2]
    assert page1["next_cursor"] == ids[1]

    page2 = (
        await client.get(
            f"/api/v1/lists?limit=2&cursor={page1['next_cursor']}", headers=auth_headers
        )
    ).json()
    assert [it["id"] for it in page2["items"]] == ids[2:]
    assert page2["next_cursor"] is None


async def test_full_page_with_no_more_items_has_null_cursor(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # Total == limit: la página viene llena pero no hay más. El cursor debe ser null (no
    # debe emitir un cursor que llevaría a una página final vacía). Ver fix keyset limit+1.
    for i in range(2):
        await client.post("/api/v1/lists", json={"title": f"L{i}"}, headers=auth_headers)
    page = (await client.get("/api/v1/lists?limit=2", headers=auth_headers)).json()
    assert len(page["items"]) == 2
    assert page["next_cursor"] is None


async def test_collaborator_editor_can_work_viewer_cannot(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # A (auth_headers) crea la lista; añade a B como editor y a C como viewer.
    list_id = (
        await client.post("/api/v1/lists", json={"title": "Equipo"}, headers=auth_headers)
    ).json()["id"]
    b_id = await _register(client, "editor@crehana.com")
    c_id = await _register(client, "viewer@crehana.com")

    add_b = await client.post(
        f"/api/v1/lists/{list_id}/members",
        json={"user_id": b_id, "role": "editor"},
        headers=auth_headers,
    )
    assert add_b.status_code == 201
    await client.post(
        f"/api/v1/lists/{list_id}/members",
        json={"user_id": c_id, "role": "viewer"},
        headers=auth_headers,
    )

    headers_b = await _login(client, "editor@crehana.com")
    headers_c = await _login(client, "viewer@crehana.com")

    # B (editor) ve la lista y puede crear tareas.
    assert (await client.get(f"/api/v1/lists/{list_id}", headers=headers_b)).status_code == 200
    created = await client.post(
        f"/api/v1/lists/{list_id}/tasks", json={"title": "t"}, headers=headers_b
    )
    assert created.status_code == 201

    # C (viewer) ve la lista pero NO puede crear tareas (403) ni gestionar miembros (403).
    assert (await client.get(f"/api/v1/lists/{list_id}", headers=headers_c)).status_code == 200
    forbidden = await client.post(
        f"/api/v1/lists/{list_id}/tasks", json={"title": "no"}, headers=headers_c
    )
    assert forbidden.status_code == 403
    manage = await client.post(
        f"/api/v1/lists/{list_id}/members",
        json={"user_id": b_id, "role": "viewer"},
        headers=headers_c,
    )
    assert manage.status_code == 403


async def test_cannot_modify_original_owner_membership(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    list_id = (
        await client.post("/api/v1/lists", json={"title": "L"}, headers=auth_headers)
    ).json()["id"]
    # El usuario autenticado es el dueño (id=1); no puede degradarse ni autoexpulsarse.
    demote = await client.put(
        f"/api/v1/lists/{list_id}/members/1",
        json={"role": "viewer"},
        headers=auth_headers,
    )
    assert demote.status_code == 403


async def test_members_lifecycle(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    list_id = (
        await client.post("/api/v1/lists", json={"title": "L"}, headers=auth_headers)
    ).json()["id"]
    collab_id = await _register(client, "collab@crehana.com")

    await client.post(
        f"/api/v1/lists/{list_id}/members",
        json={"user_id": collab_id, "role": "viewer"},
        headers=auth_headers,
    )

    members = await client.get(f"/api/v1/lists/{list_id}/members", headers=auth_headers)
    assert members.status_code == 200
    body = members.json()
    assert {m["user_id"]: m["role"] for m in body["members"]} == {1: "owner", collab_id: "viewer"}
    assert body["limit"] == 50 and body["next_cursor"] is None

    promoted = await client.put(
        f"/api/v1/lists/{list_id}/members/{collab_id}",
        json={"role": "editor"},
        headers=auth_headers,
    )
    assert promoted.status_code == 200 and promoted.json()["role"] == "editor"

    removed = await client.delete(
        f"/api/v1/lists/{list_id}/members/{collab_id}", headers=auth_headers
    )
    assert removed.status_code == 204
    # Tras quitarlo, el colaborador ya no es miembro.
    headers_collab = await _login(client, "collab@crehana.com")
    assert (await client.get(f"/api/v1/lists/{list_id}", headers=headers_collab)).status_code == 404


async def test_members_keyset_pagination(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    # Owner (id=1) + dos colaboradores: tres miembros en total.
    list_id = (
        await client.post("/api/v1/lists", json={"title": "Equipo"}, headers=auth_headers)
    ).json()["id"]
    for email in ("m1@crehana.com", "m2@crehana.com"):
        collab_id = await _register(client, email)
        await client.post(
            f"/api/v1/lists/{list_id}/members",
            json={"user_id": collab_id, "role": "viewer"},
            headers=auth_headers,
        )

    seen: list[int] = []
    cursor: int | None = None
    pages = 0
    while True:
        params = {"limit": 1} if cursor is None else {"limit": 1, "cursor": cursor}
        page = (
            await client.get(
                f"/api/v1/lists/{list_id}/members", params=params, headers=auth_headers
            )
        ).json()
        assert len(page["members"]) == 1 and page["limit"] == 1
        seen.append(page["members"][0]["user_id"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    # Recorre los tres miembros, en orden ascendente de user_id, sin repetir ni omitir.
    assert pages == 3
    assert seen == sorted(seen) and len(set(seen)) == 3


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    creds = {"email": email, "password": "supersecret123"}
    token = (await client.post("/api/v1/auth/login", json=creds)).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
