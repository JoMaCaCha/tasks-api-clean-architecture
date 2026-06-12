"""Tests de integración del outbox: entrega real con SQLite, reintentos y lifespan."""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.outbox_service import OutboxProcessor
from app.domain.enums import OutboxStatus
from app.domain.notifications import INotifier
from app.infrastructure import outbox_worker
from app.infrastructure.db.models import OutboxMessageModel
from app.infrastructure.db.repositories import SqlAlchemyOutboxRepository


class _SpyNotifier(INotifier):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def notify_task_assignment(self, *, email: str, task_title: str) -> None:
        self.calls.append((email, task_title))


class _FailingNotifier(INotifier):
    async def notify_task_assignment(self, *, email: str, task_title: str) -> None:
        raise RuntimeError("proveedor caído")


async def test_process_outbox_once_delivers_pending(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: Any
) -> None:
    async with session_factory() as session:
        await SqlAlchemyOutboxRepository(session).enqueue(
            idempotency_key="k1", email="a@e.com", task_title="t"
        )
        # enqueue hace flush; el commit lo controla la unidad de trabajo (aquí el test),
        # para que el worker lo vea en su sesión propia.
        await session.commit()

    spy = _SpyNotifier()
    monkeypatch.setattr(outbox_worker, "get_sessionmaker", lambda: session_factory)
    monkeypatch.setattr(outbox_worker, "build_notifier", lambda settings: spy)

    assert await outbox_worker.process_outbox_once() == 1
    assert spy.calls == [("a@e.com", "t")]


async def test_enqueue_is_idempotent_in_db(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Dos encolados de la misma clave **dentro de la misma transacción** (sin commit
    # intermedio): el `ON CONFLICT DO NOTHING` atómico inserta una vez y omite la otra sin
    # IntegrityError ni abortar la transacción de negocio. Es el camino crítico de la
    # carrera concurrente (misma unidad de trabajo).
    async with session_factory() as session:
        repo = SqlAlchemyOutboxRepository(session)
        assert await repo.enqueue(idempotency_key="dup", email="a@e.com", task_title="t") is True
        assert await repo.enqueue(idempotency_key="dup", email="a@e.com", task_title="t") is False
        # La transacción sigue viva y commitea sin error (no quedó abortada por el conflicto).
        await session.commit()


async def test_enqueue_rejects_unsupported_dialect() -> None:
    # El encolado idempotente depende de `ON CONFLICT`, que solo exponen PostgreSQL y SQLite.
    # Ante un dialecto no soportado debe fallar de forma **explícita** (fail-fast), en lugar de
    # asumir SQLite en silencio y emitir SQL inválido para ese motor. Se usa un stub de sesión
    # (no toca la BD): la guarda se evalúa antes de cualquier `execute`.
    from types import SimpleNamespace

    stub_session = SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name="mysql")))
    repo = SqlAlchemyOutboxRepository(stub_session)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="mysql"):
        await repo.enqueue(idempotency_key="k", email="a@e.com", task_title="t")


def test_fetch_pending_emits_for_update_skip_locked() -> None:
    # Verifica de forma determinista (compilando contra el dialecto PostgreSQL, sin BD viva)
    # que la consulta del worker bloquea las filas tomadas y salta las ya bloqueadas, que es
    # lo que garantiza que varias réplicas del worker no entreguen el mismo mensaje (ADR-15).
    from sqlalchemy.dialects import postgresql

    sql = str(SqlAlchemyOutboxRepository._pending_stmt(10).compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql


async def test_failed_delivery_retries_then_marks_failed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repo = SqlAlchemyOutboxRepository(session)
        await repo.enqueue(idempotency_key="k", email="a@e.com", task_title="t")
        processor = OutboxProcessor(outbox_repository=repo, notifier=_FailingNotifier())

        for _ in range(OutboxProcessor.MAX_ATTEMPTS):
            await processor.process_pending()

        # Agotados los reintentos: queda 'failed' y ya no se vuelve a tomar.
        assert await processor.process_pending() == 0
        message = await session.get(OutboxMessageModel, 1)
        assert message is not None
        assert message.status == OutboxStatus.FAILED
        assert message.attempts == OutboxProcessor.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_worker(monkeypatch: Any) -> None:
    # Sustituye el worker por un doble inmediato y verifica que el lifespan lo lanza y
    # lo detiene limpiamente (cubre el arranque/parada en app.web.main).
    from app.web import main

    started = False

    async def _fake_worker(stop_event: Any, interval: float = 2.0) -> None:
        nonlocal started
        started = True
        await stop_event.wait()

    monkeypatch.setattr(main, "run_outbox_worker", _fake_worker)
    app = main.create_app()
    async with main.lifespan(app):
        pass
    assert started is True
