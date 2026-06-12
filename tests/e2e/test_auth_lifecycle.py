"""E2E del ciclo de vida de la autenticación contra el servidor real (§1.b.ii, ADR-13).

Cubre, sobre HTTP real + PostgreSQL real, lo que el smoke no toca: rotación de refresh con
detección de reúso, logout de una sesión, logout global (invalida el access vigente) y los
caminos de error (registro duplicado, contraseña incorrecta, token inválido).

Nota: el límite por IP (429) se valida de forma determinista en la suite de integración; no
se reproduce aquí porque exigiría un umbral bajo que envenenaría la ventana por IP del
servidor compartido y rompería el aislamiento entre pruebas E2E.
"""

import httpx
import pytest

from tests.e2e.helpers import PASSWORD, login, new_user, register, unique_email

pytestmark = pytest.mark.e2e


async def test_register_duplicate_returns_409(client: httpx.AsyncClient) -> None:
    email = unique_email("dup")
    await register(client, email)
    again = await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert again.status_code == 409


async def test_login_wrong_password_returns_401(client: httpx.AsyncClient) -> None:
    email = unique_email("wrongpw")
    await register(client, email)
    bad = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "incorrecta-123"}
    )
    assert bad.status_code == 401


async def test_bad_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/lists", headers={"Authorization": "Bearer token-basura"})
    assert response.status_code == 401


async def test_refresh_rotation_and_reuse_detection(client: httpx.AsyncClient) -> None:
    email = unique_email("refresh")
    await register(client, email)
    tokens = await login(client, email)

    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert rotated.status_code == 200
    new_tokens = rotated.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # Reusar el refresh viejo (ya rotado) se rechaza y revoca toda la cadena de la sesión.
    reused = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401
    after_reuse = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert after_reuse.status_code == 401  # la revocación por reúso persistió en PostgreSQL


async def test_logout_revokes_single_session(client: httpx.AsyncClient) -> None:
    email = unique_email("logout")
    await register(client, email)
    tokens = await login(client, email)
    logout = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert logout.status_code == 204
    retry = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert retry.status_code == 401


async def test_logout_all_invalidates_access_token(client: httpx.AsyncClient) -> None:
    _, headers = await new_user(client, "logoutall")
    assert (await client.get("/api/v1/lists", headers=headers)).status_code == 200
    assert (await client.post("/api/v1/auth/logout-all", headers=headers)).status_code == 204
    # logout-all sube token_version → el access emitido antes queda invalidado.
    assert (await client.get("/api/v1/lists", headers=headers)).status_code == 401
