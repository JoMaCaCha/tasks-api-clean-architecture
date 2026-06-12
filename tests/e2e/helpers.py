"""Utilidades compartidas por las pruebas E2E (registro/login contra el servidor real)."""

from uuid import uuid4

import httpx

PASSWORD = "supersecret123"


def etag(response: httpx.Response) -> str:
    """ETag de una respuesta de tarea, para reenviarlo en `If-Match` (ADR-26)."""
    value = response.headers.get("etag")
    assert value is not None, f"Se esperaba cabecera ETag en {response.request.url}"
    return value


def unique_email(prefix: str = "e2e") -> str:
    """Email único por corrida: el PostgreSQL real es persistente entre ejecuciones."""
    return f"{prefix}-{uuid4().hex}@crehana.com"


async def register(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> int:
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    tokens: dict[str, str] = response.json()
    return tokens


async def new_user(client: httpx.AsyncClient, prefix: str = "e2e") -> tuple[int, dict[str, str]]:
    """Registra un usuario nuevo e inicia sesión. Devuelve ``(user_id, cabeceras_auth)``."""
    email = unique_email(prefix)
    user_id = await register(client, email)
    tokens = await login(client, email)
    return user_id, {"Authorization": f"Bearer {tokens['access_token']}"}
