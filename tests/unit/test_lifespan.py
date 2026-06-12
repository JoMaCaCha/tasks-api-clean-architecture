"""Tests del `lifespan`: arranque condicional del worker de outbox en proceso (ADR-15).

Se sustituyen el motor (para no abrir conexiones reales) y `run_outbox_worker` (para no
tocar la BD), verificando solo la decisión de lanzar —o no— el worker según
`RUN_OUTBOX_WORKER_IN_PROCESS`.
"""

import asyncio
from typing import Any

from fastapi import FastAPI

from app.infrastructure.config import Settings
from app.web import main as web_main


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


def _patch_engine_and_worker(monkeypatch: Any) -> dict[str, Any]:
    state: dict[str, Any] = {"started": False, "cleanup_started": False, "engine": _FakeEngine()}

    async def _fake_worker(stop_event: asyncio.Event) -> None:
        state["started"] = True
        await stop_event.wait()

    async def _fake_cleanup(stop_event: asyncio.Event, interval: float = 3600.0) -> None:
        state["cleanup_started"] = True
        await stop_event.wait()

    monkeypatch.setattr(web_main, "get_engine", lambda: state["engine"])
    monkeypatch.setattr(web_main, "run_outbox_worker", _fake_worker)
    monkeypatch.setattr(web_main, "run_token_cleanup_worker", _fake_cleanup)
    return state


async def test_lifespan_starts_in_process_worker_by_default(monkeypatch: Any) -> None:
    state = _patch_engine_and_worker(monkeypatch)
    monkeypatch.setattr(
        web_main, "get_settings", lambda: Settings(run_outbox_worker_in_process=True)
    )
    app = FastAPI()
    async with web_main.lifespan(app):
        # Cede el control para que la tarea del worker llegue a ejecutarse.
        await asyncio.sleep(0)
    assert state["started"] is True
    assert state["engine"].disposed is True


async def test_lifespan_skips_in_process_worker_when_disabled(monkeypatch: Any) -> None:
    state = _patch_engine_and_worker(monkeypatch)
    monkeypatch.setattr(
        web_main, "get_settings", lambda: Settings(run_outbox_worker_in_process=False)
    )
    app = FastAPI()
    async with web_main.lifespan(app):
        await asyncio.sleep(0)
    assert state["started"] is False
    assert state["engine"].disposed is True


async def test_lifespan_starts_token_cleanup_by_default(monkeypatch: Any) -> None:
    state = _patch_engine_and_worker(monkeypatch)
    monkeypatch.setattr(
        web_main, "get_settings", lambda: Settings(run_token_cleanup_in_process=True)
    )
    app = FastAPI()
    async with web_main.lifespan(app):
        await asyncio.sleep(0)
    assert state["cleanup_started"] is True
    assert state["engine"].disposed is True


async def test_lifespan_skips_token_cleanup_when_disabled(monkeypatch: Any) -> None:
    state = _patch_engine_and_worker(monkeypatch)
    monkeypatch.setattr(
        web_main, "get_settings", lambda: Settings(run_token_cleanup_in_process=False)
    )
    app = FastAPI()
    async with web_main.lifespan(app):
        await asyncio.sleep(0)
    assert state["cleanup_started"] is False
    assert state["engine"].disposed is True
