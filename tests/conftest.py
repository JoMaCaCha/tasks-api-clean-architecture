"""Fixtures compartidas: motor SQLite async en memoria y cliente HTTP de la API."""

import os
from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool

from app.domain.exceptions import DomainError
from app.infrastructure.db import Base  # importar el paquete registra los modelos ORM
from app.infrastructure.db.session import get_session
from app.web.main import create_app

# `JWT_SECRET_KEY` es obligatorio (app/infrastructure/config.py). Ningún módulo de la app
# lo lee al importarse (config y motor son perezosos), así que basta definirlo aquí para
# que los tests sean herméticos, sin depender de un archivo .env.
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-with-at-least-32-chars")

# Base de datos de la suite. Por defecto, SQLite async en memoria (rápida y hermética). Si
# se define `TEST_DATABASE_URL` (p. ej. `postgresql+asyncpg://...`) la misma suite corre
# contra esa base, para validar los caminos que dependen del dialecto sin re-escribir los
# tests: el `INSERT ... ON CONFLICT` idempotente del outbox y el `FOR UPDATE SKIP LOCKED`.
# En CI un job dedicado la apunta a un PostgreSQL real (ver .github/workflows/ci.yml).
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite://")


def _create_test_engine() -> AsyncEngine:
    """Crea el motor de pruebas adaptando el *pool* al backend.

    - **SQLite en memoria**: `StaticPool` + `check_same_thread=False` para que las múltiples
      sesiones de la app compartan la única conexión donde vive la base (sin esto, cada
      sesión vería una `:memory:` distinta y vacía).
    - **PostgreSQL u otro servidor**: `NullPool` (sin reutilizar conexiones entre tests),
      recomendado para entornos de prueba con esquema recreado por test.
    """
    if TEST_DATABASE_URL.startswith("sqlite"):
        return create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = _create_test_engine()
    # `drop_all` previo: limpia un esquema dejado por un test anterior que falló a mitad
    # (no-op en SQLite en memoria, que arranca vacío). Recrear por test aísla cada caso.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def app(session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    """App con la sesión apuntando a SQLite en memoria.

    Se expone como fixture para que los tests puedan sobrescribir otras dependencias
    (p. ej. el notificador) sobre la misma instancia que usa el `client`.
    """
    application = create_app()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        # Espejo de `get_session`: la request es la unidad de trabajo (commit al final si
        # no hubo excepción), para que las escrituras de los repos (que solo hacen flush)
        # se persistan de forma atómica igual que en producción.
        async with session_factory() as session:
            try:
                yield session
            except DomainError as exc:
                # Espejo de `get_session`: por defecto revierte; solo persiste si el error
                # pide `commit_side_effects=True` (efecto deliberado antes de rechazar,
                # p. ej. revocar la sesión por reúso de un refresh token).
                if exc.commit_side_effects and session.in_transaction():
                    await session.commit()
                elif session.in_transaction():
                    await session.rollback()
                raise
            except Exception:
                await session.rollback()
                raise
            else:
                if session.in_transaction():
                    await session.commit()

    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Registra e inicia sesión, devolviendo cabeceras Authorization válidas."""
    credentials = {"email": "tester@crehana.com", "password": "supersecret123"}
    await client.post("/api/v1/auth/register", json=credentials)
    response = await client.post("/api/v1/auth/login", json=credentials)
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
