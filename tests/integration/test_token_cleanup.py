"""Integración: purga de refresh tokens expirados sobre el repositorio real (ADR-21).

Valida el `DELETE WHERE expires_at < now` y la unidad de trabajo de
`cleanup_expired_refresh_tokens_once` contra el motor real (SQLite async por defecto;
PostgreSQL real en el job dedicado de CI). Se crea un usuario real porque PostgreSQL
exige la FK `refresh_tokens.user_id → users.id`.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure import maintenance
from app.infrastructure.db.repositories import (
    SqlAlchemyRefreshTokenRepository,
    SqlAlchemyUserRepository,
)


async def _seed_tokens(session_factory: async_sessionmaker[AsyncSession], now: datetime) -> None:
    async with session_factory() as session:
        user = await SqlAlchemyUserRepository(session).create(
            email="cleanup@example.com", hashed_password="x"
        )
        repo = SqlAlchemyRefreshTokenRepository(session)
        await repo.add(user_id=user.id, token_hash="expired", expires_at=now - timedelta(days=1))
        await repo.add(user_id=user.id, token_hash="valid", expires_at=now + timedelta(days=7))
        await session.commit()


async def test_delete_expired_removes_only_expired(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    await _seed_tokens(session_factory, now)

    async with session_factory() as session:
        repo = SqlAlchemyRefreshTokenRepository(session)
        removed = await repo.delete_expired(now=now)
        await session.commit()

        assert removed == 1
        assert await repo.get_by_hash("expired") is None
        assert await repo.get_by_hash("valid") is not None


async def test_cleanup_once_commits_and_reports_count(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: Any
) -> None:
    now = datetime.now(UTC)
    await _seed_tokens(session_factory, now)

    # `cleanup_expired_refresh_tokens_once` abre su propia sesión vía `get_sessionmaker`;
    # se apunta a la fábrica de pruebas para ejercitar su unidad de trabajo (commit propio).
    monkeypatch.setattr(maintenance, "get_sessionmaker", lambda: session_factory)
    removed = await maintenance.cleanup_expired_refresh_tokens_once()
    assert removed == 1

    async with session_factory() as session:
        repo = SqlAlchemyRefreshTokenRepository(session)
        assert await repo.get_by_hash("expired") is None
        assert await repo.get_by_hash("valid") is not None
