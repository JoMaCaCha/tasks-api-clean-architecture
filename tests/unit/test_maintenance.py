"""Tests del worker de mantenimiento: purga de refresh tokens expirados (ADR-21).

El bucle se prueba sin BD (se sustituye la pasada de purga); la purga real sobre el
repositorio se valida en `tests/integration/test_token_cleanup.py`.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.infrastructure import maintenance
from tests.unit.fakes import FakeRefreshTokenRepository


async def test_delete_expired_removes_only_expired_tokens() -> None:
    repo = FakeRefreshTokenRepository()
    now = datetime.now(UTC)
    await repo.add(user_id=1, token_hash="old", expires_at=now - timedelta(seconds=1))
    await repo.add(user_id=1, token_hash="fresh", expires_at=now + timedelta(days=7))

    removed = await repo.delete_expired(now=now)

    assert removed == 1
    assert await repo.get_by_hash("old") is None
    assert await repo.get_by_hash("fresh") is not None


async def test_cleanup_worker_loop_runs_until_stopped(monkeypatch: Any) -> None:
    calls: list[int] = []
    stop = asyncio.Event()

    async def _cleanup() -> int:
        calls.append(1)
        stop.set()  # detener tras la primera pasada
        return 0

    monkeypatch.setattr(maintenance, "cleanup_expired_refresh_tokens_once", _cleanup)
    await maintenance.run_token_cleanup_worker(stop, interval=0.01)
    assert len(calls) == 1


async def test_cleanup_worker_survives_errors(monkeypatch: Any) -> None:
    calls: list[int] = []
    stop = asyncio.Event()

    async def _boom() -> int:
        calls.append(1)
        if len(calls) >= 2:
            stop.set()
        raise RuntimeError("fallo transitorio")

    monkeypatch.setattr(maintenance, "cleanup_expired_refresh_tokens_once", _boom)
    await maintenance.run_token_cleanup_worker(stop, interval=0.01)
    assert len(calls) >= 2  # un error no mata el worker; reintenta en la siguiente vuelta
