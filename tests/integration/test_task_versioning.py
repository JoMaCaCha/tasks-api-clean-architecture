"""Regresión de ADR-31: una escritura no puede pisar otra confirmada después de la lectura.

El servicio valida `If-Match` contra la versión que leyó, y el repositorio solo debe escribir si
la fila **sigue en esa versión**. Antes, `session.get` podía releer la fila (el identity map guarda
referencias débiles) y `version_id_col` filtraba por la versión ya incrementada por la otra
transacción: ambas escrituras respondían 200 (*lost update*). Las E2E lo veían de forma
intermitente; aquí se reproduce de forma determinista con dos sesiones.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import TaskPriority, TaskStatus
from app.domain.exceptions import PreconditionFailedError
from app.infrastructure.db.repositories import (
    SqlAlchemyTaskListRepository,
    SqlAlchemyTaskRepository,
    SqlAlchemyUserRepository,
)


async def _seed_task(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        user = await SqlAlchemyUserRepository(session).create(
            email="occ-repo@example.com", hashed_password="x"
        )
        task_list = await SqlAlchemyTaskListRepository(session).create(
            owner_id=user.id, title="OCC", description=None
        )
        task = await SqlAlchemyTaskRepository(session).create(
            list_id=task_list.id,
            title="t",
            description=None,
            status=TaskStatus.PENDING,
            priority=TaskPriority.MEDIUM,
            assignee_id=None,
        )
        await session.commit()
        return task.id


async def _commit_concurrent_write(
    session_factory: async_sessionmaker[AsyncSession], task_id: int
) -> None:
    """Otra transacción modifica la tarea y confirma (v1 → v2)."""
    async with session_factory() as session:
        await SqlAlchemyTaskRepository(session).update(
            task_id,
            expected_version=1,
            title="t",
            description=None,
            status=TaskStatus.DONE,
            priority=TaskPriority.MEDIUM,
            assignee_id=None,
        )
        await session.commit()


async def test_update_rejects_version_changed_after_read(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    task_id = await _seed_task(session_factory)
    async with session_factory() as session:
        repo = SqlAlchemyTaskRepository(session)
        seen = await repo.get(task_id)  # el servicio lee v1 y valida If-Match contra ella
        assert seen is not None and seen.version == 1

        await _commit_concurrent_write(session_factory, task_id)

        with pytest.raises(PreconditionFailedError):
            await repo.update(
                task_id,
                expected_version=seen.version,
                title=seen.title,
                description=seen.description,
                status=TaskStatus.IN_PROGRESS,
                priority=seen.priority,
                assignee_id=seen.assignee_id,
            )


async def test_delete_rejects_version_changed_after_read(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    task_id = await _seed_task(session_factory)
    async with session_factory() as session:
        repo = SqlAlchemyTaskRepository(session)
        seen = await repo.get(task_id)
        assert seen is not None

        await _commit_concurrent_write(session_factory, task_id)

        with pytest.raises(PreconditionFailedError):
            await repo.delete(task_id, expected_version=seen.version)
