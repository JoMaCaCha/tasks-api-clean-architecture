"""Motor y fábrica de sesiones async, y dependencia `get_session`.

El motor y la fábrica de sesiones se construyen de forma **perezosa** (en el primer uso,
no al importar el módulo). Así, importar la aplicación no exige que la configuración
esté presente —en particular el `JWT_SECRET_KEY` obligatorio—, lo que mantiene los
tests herméticos: sobrescriben `get_session` y nunca llegan a crear el motor real.
"""

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.domain.exceptions import DomainError
from app.infrastructure.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Crea (una sola vez) el motor async a partir de `DATABASE_URL`."""
    return create_async_engine(get_settings().database_url, future=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Fábrica de sesiones async ligada al motor perezoso."""
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provee una sesión async por request como **unidad de trabajo**.

    Los repositorios solo hacen ``flush`` (no ``commit``): el límite transaccional lo
    fija aquí la request. Si el endpoint termina sin excepción se hace **un único
    commit** (todas las escrituras —incluido el encolado en el outbox— son atómicas);
    si lanza, se revierte todo. Así la inserción en el outbox y la escritura de negocio
    comparten transacción (patrón outbox transaccional; ver DECISION_LOG ADR-15).
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
        except DomainError as exc:
            # Un error de dominio es un resultado de negocio (4xx). El **default es seguro**:
            # se revierte cualquier escritura previa, salvo que el error pida explícitamente
            # persistir sus efectos con `commit_side_effects=True` (p. ej. la revocación de
            # sesión al detectar reúso de un refresh token). Antes esto era implícito
            # (siempre commit) y dependía de una invariante frágil "no escribas antes de
            # lanzar"; ahora persistir es una decisión explícita del caso de uso, así que un
            # nuevo error de validación no puede filtrar escrituras por accidente.
            if exc.commit_side_effects and session.in_transaction():
                await session.commit()
            elif session.in_transaction():
                await session.rollback()
            raise
        except Exception:
            # Fallo inesperado (bug/infraestructura): se descarta cualquier escritura.
            await session.rollback()
            raise
        else:
            # Solo si hay una transacción activa: un endpoint de solo lectura que ya
            # revirtió (p. ej. readiness con la BD caída) la deja cerrada y se omite.
            if session.in_transaction():
                await session.commit()
