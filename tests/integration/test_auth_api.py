"""Tests de integración del flujo de autenticación y protección de endpoints."""

from collections.abc import AsyncIterator

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError

from app.infrastructure.db.session import get_session
from app.infrastructure.security.rate_limiter import InMemorySlidingWindowRateLimiter
from app.web.main import create_app


async def test_health_is_public(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_checks_database(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_readiness_returns_503_when_db_unreachable() -> None:
    class _FailingSession:
        async def execute(self, *args: object, **kwargs: object) -> None:
            raise SQLAlchemyError("base de datos no disponible")

        async def rollback(self) -> None:
            # readiness revierte la transacción fallida antes de devolver 503.
            return None

    app = create_app()

    async def override_get_session() -> AsyncIterator[_FailingSession]:
        yield _FailingSession()

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


async def test_register_login_flow(client: AsyncClient) -> None:
    creds = {"email": "new@example.com", "password": "supersecret123"}
    register = await client.post("/api/v1/auth/register", json=creds)
    assert register.status_code == 201
    assert register.json()["email"] == creds["email"]
    assert "hashed_password" not in register.json()

    login = await client.post("/api/v1/auth/login", json=creds)
    assert login.status_code == 200
    body = login.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]


async def test_refresh_rotates_and_old_token_is_rejected(client: AsyncClient) -> None:
    creds = {"email": "refresh@example.com", "password": "supersecret123"}
    await client.post("/api/v1/auth/register", json=creds)
    tokens = (await client.post("/api/v1/auth/login", json=creds)).json()

    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert rotated.status_code == 200
    new_tokens = rotated.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # Reusar el refresh viejo (ya rotado) se rechaza y revoca la sesión.
    reused = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401
    after_reuse = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert after_reuse.status_code == 401  # toda la cadena queda revocada


async def test_logout_revokes_refresh_token(client: AsyncClient) -> None:
    creds = {"email": "logout@example.com", "password": "supersecret123"}
    await client.post("/api/v1/auth/register", json=creds)
    tokens = (await client.post("/api/v1/auth/login", json=creds)).json()

    logout = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert logout.status_code == 204
    retry = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert retry.status_code == 401


async def test_logout_all_invalidates_access_token(client: AsyncClient) -> None:
    creds = {"email": "logoutall@example.com", "password": "supersecret123"}
    await client.post("/api/v1/auth/register", json=creds)
    tokens = (await client.post("/api/v1/auth/login", json=creds)).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # El access token funciona...
    assert (await client.get("/api/v1/lists", headers=headers)).status_code == 200
    # ...hasta el logout global, que sube token_version e invalida el access vigente.
    assert (await client.post("/api/v1/auth/logout-all", headers=headers)).status_code == 204
    assert (await client.get("/api/v1/lists", headers=headers)).status_code == 401


async def test_register_duplicate_returns_409(client: AsyncClient) -> None:
    creds = {"email": "dup@example.com", "password": "supersecret123"}
    await client.post("/api/v1/auth/register", json=creds)
    second = await client.post("/api/v1/auth/register", json=creds)
    assert second.status_code == 409


async def test_register_rejects_password_over_72_bytes(client: AsyncClient) -> None:
    # bcrypt ignora todo lo que pase de 72 bytes; en vez de truncar en silencio (lo que
    # dejaría dos contraseñas distintas con el mismo hash) la validación lo rechaza con 422.
    # Ver ADR-22 y OWASP Password Storage Cheat Sheet.
    creds = {"email": "long@example.com", "password": "a" * 73}
    response = await client.post("/api/v1/auth/register", json=creds)
    assert response.status_code == 422


async def test_register_accepts_password_at_72_byte_boundary(client: AsyncClient) -> None:
    # El límite es inclusivo: exactamente 72 bytes se acepta y permite iniciar sesión.
    creds = {"email": "boundary@example.com", "password": "a" * 72}
    assert (await client.post("/api/v1/auth/register", json=creds)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=creds)).status_code == 200


async def test_login_wrong_password_returns_401(client: AsyncClient) -> None:
    creds = {"email": "x@example.com", "password": "supersecret123"}
    await client.post("/api/v1/auth/register", json=creds)
    bad = await client.post(
        "/api/v1/auth/login", json={"email": creds["email"], "password": "nope12345"}
    )
    assert bad.status_code == 401


async def test_login_with_overlong_password_returns_401_not_500(client: AsyncClient) -> None:
    # `RegisterRequest` rechaza contraseñas de >72 bytes (ADR-22), pero `LoginRequest` no
    # impone ese tope (un intento de login es entrada no confiable). bcrypt 5.x **lanza**
    # `ValueError` ante >72 bytes (antes truncaba en silencio), así que sin el recorte
    # defensivo del hasher (`_encode`) este intento reventaría en 500. El backstop lo
    # convierte en un 401 normal (credenciales inválidas). Regresión que fija ese contrato.
    creds = {"email": "overlong-login@example.com", "password": "supersecret123"}
    await client.post("/api/v1/auth/register", json=creds)
    attempt = await client.post(
        "/api/v1/auth/login", json={"email": creds["email"], "password": "a" * 200}
    )
    assert attempt.status_code == 401


async def test_login_is_rate_limited_returns_429(app: FastAPI, client: AsyncClient) -> None:
    # Umbral bajo para esta prueba: 2 intentos por ventana antes de bloquear.
    app.state.login_rate_limiter = InMemorySlidingWindowRateLimiter(
        max_attempts=2, window_seconds=60
    )
    creds = {"email": "brute@example.com", "password": "supersecret123"}
    await client.post("/api/v1/auth/register", json=creds)

    assert (await client.post("/api/v1/auth/login", json=creds)).status_code == 200
    assert (await client.post("/api/v1/auth/login", json=creds)).status_code == 200
    blocked = await client.post("/api/v1/auth/login", json=creds)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0  # informa cuándo reintentar


async def test_refresh_is_rate_limited_returns_429(app: FastAPI, client: AsyncClient) -> None:
    # `/auth/refresh` también se limita por IP (ADR-17/ADR-20 sobre seguridad de tokens): un
    # umbral bajo bloquea el barrido de refresh tokens tras 2 intentos por ventana. El scope
    # "refresh" tiene su propia clave, independiente del de login.
    app.state.login_rate_limiter = InMemorySlidingWindowRateLimiter(
        max_attempts=2, window_seconds=60
    )
    first = await client.post("/api/v1/auth/refresh", json={"refresh_token": "invalido"})
    second = await client.post("/api/v1/auth/refresh", json={"refresh_token": "invalido"})
    assert first.status_code == 401 and second.status_code == 401  # token inválido, aún no bloquea
    blocked = await client.post("/api/v1/auth/refresh", json={"refresh_token": "invalido"})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


async def test_logout_is_rate_limited_returns_429(app: FastAPI, client: AsyncClient) -> None:
    # `/auth/logout` es público y escribe en BD; se limita por IP igual que el resto de
    # `/auth/*` (ADR-17). El scope "logout" tiene su propia clave. Tras 2 intentos, 429.
    app.state.login_rate_limiter = InMemorySlidingWindowRateLimiter(
        max_attempts=2, window_seconds=60
    )
    body = {"refresh_token": "token-cualquiera"}
    # Revocar un hash inexistente es un no-op idempotente: responde 204 igualmente.
    assert (await client.post("/api/v1/auth/logout", json=body)).status_code == 204
    assert (await client.post("/api/v1/auth/logout", json=body)).status_code == 204
    blocked = await client.post("/api/v1/auth/logout", json=body)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


async def test_protected_endpoint_without_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/lists")
    assert response.status_code == 401


async def test_protected_endpoint_with_bad_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/lists", headers={"Authorization": "Bearer garbage"})
    assert response.status_code == 401
