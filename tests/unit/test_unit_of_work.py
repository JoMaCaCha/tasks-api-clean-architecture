"""Invariante de la unidad de trabajo por request (`get_session`).

Verifica que una escritura seguida de un `DomainError` se **revierte por defecto**, y que
solo persiste cuando el error pide explícitamente `commit_side_effects=True`. Así, añadir
un nuevo rechazo de validación no puede filtrar escrituras por accidente (ver el riesgo de
diseño documentado en `app/infrastructure/db/session.py`).
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.domain.exceptions import AuthError, NotFoundError
from app.infrastructure.db import Base  # importar el paquete registra los modelos ORM
from app.infrastructure.db import session as session_module
from app.infrastructure.db.models import UserModel


@pytest_asyncio.fixture
async def session_factory(monkeypatch: pytest.MonkeyPatch) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    # `get_session` resuelve la fábrica de sesiones de forma perezosa; la sustituimos por
    # la de SQLite en memoria para ejercer la lógica real de commit/rollback de la request.
    monkeypatch.setattr(session_module, "get_sessionmaker", lambda: factory)
    return factory


async def _count_users(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as check:
        return len((await check.execute(select(UserModel))).scalars().all())


async def test_plain_domain_error_rolls_back_writes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gen = session_module.get_session()
    db = await gen.__anext__()
    db.add(UserModel(email="rollback@e.com", hashed_password="h"))
    await db.flush()
    # Un DomainError corriente (sin opt-in) debe revertir: nada se persiste.
    with pytest.raises(NotFoundError):
        await gen.athrow(NotFoundError("rechazo de validación"))
    assert await _count_users(session_factory) == 0


async def test_flagged_domain_error_commits_side_effects(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gen = session_module.get_session()
    db = await gen.__anext__()
    db.add(UserModel(email="persist@e.com", hashed_password="h"))
    await db.flush()
    # `commit_side_effects=True` (p. ej. revocación por reúso de refresh): debe persistir.
    with pytest.raises(AuthError):
        await gen.athrow(AuthError("efecto deliberado", commit_side_effects=True))
    assert await _count_users(session_factory) == 1


async def test_unexpected_error_rolls_back_writes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Un error NO-dominio (bug/infraestructura) descarta toda escritura (rama `except`)."""
    gen = session_module.get_session()
    db = await gen.__anext__()
    db.add(UserModel(email="infra-boom@e.com", hashed_password="h"))
    await db.flush()
    # Cualquier excepción que no sea DomainError no es un resultado de negocio: se revierte.
    with pytest.raises(RuntimeError):
        await gen.athrow(RuntimeError("fallo de infraestructura"))
    assert await _count_users(session_factory) == 0


async def test_clean_exit_commits_writes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Sin excepción, la request hace **un commit** al cerrar la sesión (rama `else`)."""
    gen = session_module.get_session()
    db = await gen.__anext__()
    db.add(UserModel(email="commit@e.com", hashed_password="h"))
    await db.flush()
    # Agotar el generador (sin lanzar) ejecuta el código tras el `yield`: el commit final.
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    assert await _count_users(session_factory) == 1


async def test_get_sessionmaker_builds_a_factory_from_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cubre la construcción perezosa de la fábrica de sesiones (`get_sessionmaker`).

    Los demás tests sustituyen `get_sessionmaker` por completo, así que su cuerpo nunca se
    ejecutaba. Aquí se fuerza limpiando su caché `lru_cache` y se evita el motor PostgreSQL
    real sustituyendo `get_engine` por uno SQLite en memoria.
    """
    engine = create_async_engine("sqlite+aiosqlite://")
    session_module.get_sessionmaker.cache_clear()
    monkeypatch.setattr(session_module, "get_engine", lambda: engine)
    try:
        maker = session_module.get_sessionmaker()
        assert isinstance(maker, async_sessionmaker)
    finally:
        # No dejar la fábrica SQLite cacheada para los siguientes tests; liberar el motor.
        session_module.get_sessionmaker.cache_clear()
        await engine.dispose()
