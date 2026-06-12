"""Tests unitarios del OutboxProcessor (entrega, idempotencia y reintentos)."""

from app.application.outbox_service import OutboxProcessor
from app.domain.enums import OutboxStatus
from app.domain.notifications import INotifier
from tests.unit.fakes import FakeOutboxRepository


class _SpyNotifier(INotifier):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def notify_task_assignment(self, *, email: str, task_title: str) -> None:
        self.calls.append((email, task_title))


class _FailingNotifier(INotifier):
    async def notify_task_assignment(self, *, email: str, task_title: str) -> None:
        raise RuntimeError("proveedor caído")


async def test_processes_pending_and_marks_sent() -> None:
    outbox = FakeOutboxRepository()
    await outbox.enqueue(idempotency_key="k1", email="a@e.com", task_title="t")
    spy = _SpyNotifier()
    processor = OutboxProcessor(outbox_repository=outbox, notifier=spy)

    processed = await processor.process_pending()
    assert processed == 1
    assert spy.calls == [("a@e.com", "t")]
    assert outbox.items[0].status == OutboxStatus.SENT
    # Ya entregado: una segunda pasada no reenvía.
    assert await processor.process_pending() == 0
    assert spy.calls == [("a@e.com", "t")]


async def test_enqueue_is_idempotent() -> None:
    outbox = FakeOutboxRepository()
    assert await outbox.enqueue(idempotency_key="k", email="a@e.com", task_title="t") is True
    assert await outbox.enqueue(idempotency_key="k", email="a@e.com", task_title="t") is False
    assert len(outbox.items) == 1


async def test_retries_then_marks_failed() -> None:
    outbox = FakeOutboxRepository()
    await outbox.enqueue(idempotency_key="k", email="a@e.com", task_title="t")
    processor = OutboxProcessor(outbox_repository=outbox, notifier=_FailingNotifier())

    # Cada pasada reintenta hasta agotar MAX_ATTEMPTS y marcar 'failed'.
    for _ in range(OutboxProcessor.MAX_ATTEMPTS):
        await processor.process_pending()

    message = outbox.items[0]
    assert message.status == OutboxStatus.FAILED
    assert message.attempts == OutboxProcessor.MAX_ATTEMPTS
    assert message.last_error is not None
    assert await processor.process_pending() == 0  # 'failed' ya no se reintenta
