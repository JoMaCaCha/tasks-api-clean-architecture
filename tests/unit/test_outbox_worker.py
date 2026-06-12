"""Tests del bucle del worker de outbox (sin BD: se sustituye el procesamiento)."""

import asyncio
from typing import Any

from app.infrastructure import outbox_worker


async def test_worker_loop_runs_until_stopped(monkeypatch: Any) -> None:
    calls: list[int] = []
    stop = asyncio.Event()

    async def _process() -> int:
        calls.append(1)
        stop.set()  # detener tras la primera tanda
        return 0

    monkeypatch.setattr(outbox_worker, "process_outbox_once", _process)
    await outbox_worker.run_outbox_worker(stop, interval=0.01)
    assert len(calls) == 1


async def test_worker_loop_survives_processing_errors(monkeypatch: Any) -> None:
    calls: list[int] = []
    stop = asyncio.Event()

    async def _boom() -> int:
        calls.append(1)
        if len(calls) >= 2:
            stop.set()
        raise RuntimeError("fallo transitorio")

    monkeypatch.setattr(outbox_worker, "process_outbox_once", _boom)
    await outbox_worker.run_outbox_worker(stop, interval=0.01)
    assert len(calls) >= 2  # un error no mata el worker; reintenta en la siguiente vuelta


# --- Entrypoint del worker dedicado (ADR-15) ---------------------------------


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


async def test_serve_runs_worker_and_disposes_engine(monkeypatch: Any) -> None:
    engine = _FakeEngine()
    ran: list[int] = []

    async def _fake_worker(stop_event: asyncio.Event) -> None:
        ran.append(1)

    monkeypatch.setattr(outbox_worker, "_install_signal_handlers", lambda loop, ev: None)
    monkeypatch.setattr(outbox_worker, "run_outbox_worker", _fake_worker)
    monkeypatch.setattr(outbox_worker, "get_engine", lambda: engine)

    await outbox_worker._serve()
    assert ran == [1]
    assert engine.disposed is True  # el motor se libera aunque el worker termine/falle


def test_main_invokes_serve(monkeypatch: Any) -> None:
    called: list[int] = []

    async def _fake_serve() -> None:
        called.append(1)

    monkeypatch.setattr(outbox_worker, "_serve", _fake_serve)
    outbox_worker.main()  # envuelve asyncio.run(_serve())
    assert called == [1]


def test_install_signal_handlers_registers_both_signals() -> None:
    registered: list[int] = []

    class _Loop:
        def add_signal_handler(self, sig: int, cb: Any) -> None:
            registered.append(sig)

    outbox_worker._install_signal_handlers(_Loop(), asyncio.Event())  # type: ignore[arg-type]
    assert len(registered) == 2  # SIGINT + SIGTERM


def test_install_signal_handlers_falls_back_when_unsupported(monkeypatch: Any) -> None:
    # En plataformas sin add_signal_handler (p. ej. Windows) se degrada a signal.signal.
    fallbacks: list[int] = []

    class _Loop:
        def add_signal_handler(self, sig: int, cb: Any) -> None:
            raise NotImplementedError

    monkeypatch.setattr(outbox_worker.signal, "signal", lambda s, h: fallbacks.append(s))
    outbox_worker._install_signal_handlers(_Loop(), asyncio.Event())  # type: ignore[arg-type]
    assert len(fallbacks) == 2

    # Y el handler instalado activa el evento de parada.
    stop = asyncio.Event()
    captured: dict[int, Any] = {}

    class _Loop2:
        def add_signal_handler(self, sig: int, cb: Any) -> None:
            raise NotImplementedError

    monkeypatch.setattr(outbox_worker.signal, "signal", lambda s, h: captured.setdefault(s, h))
    outbox_worker._install_signal_handlers(_Loop2(), stop)  # type: ignore[arg-type]
    next(iter(captured.values()))()  # simula la recepción de la señal
    assert stop.is_set()
