"""Punto de entrada de la API: factoría de la app, lifespan y registro de routers."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.domain.security import ILoginRateLimiter
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.db.session import get_engine
from app.infrastructure.logging_config import configure_logging
from app.infrastructure.maintenance import run_token_cleanup_worker
from app.infrastructure.outbox_worker import run_outbox_worker
from app.infrastructure.security.rate_limiter import InMemorySlidingWindowRateLimiter
from app.web.client_ip import parse_trusted_proxies
from app.web.errors import register_exception_handlers
from app.web.routers import auth, health, lists, tasks

# Versionado por prefijo de ruta. La política de evolución (cuándo nace `/api/v2` y cómo se
# retira `v1` con cabeceras `Deprecation`/`Sunset`) está documentada en DECISION_LOG ADR-24.
API_PREFIX = "/api/v1"

logger = logging.getLogger("app.web")


def _build_rate_limiter(app: FastAPI, settings: Settings) -> ILoginRateLimiter:
    """Construye el limitador según `RATE_LIMITER_BACKEND` (`memory` por defecto).

    Con `redis` el contador es compartido entre réplicas; con `memory` es por proceso
    (ver DECISION_LOG ADR-17). El cliente Redis se guarda en `app.state` para cerrarlo en
    el shutdown. La importación de Redis es perezosa: un despliegue `memory` no la necesita.
    """
    if settings.rate_limiter_backend.lower() == "redis":
        from redis.asyncio import from_url

        from app.infrastructure.security.redis_rate_limiter import (
            RedisSlidingWindowRateLimiter,
        )

        redis_client = from_url(settings.redis_url)
        app.state.redis_client = redis_client
        logger.info("Limitador de login: backend=redis (contador compartido entre réplicas).")
        return RedisSlidingWindowRateLimiter(
            redis=redis_client,
            max_attempts=settings.login_rate_limit_max_attempts,
            window_seconds=settings.login_rate_limit_window_seconds,
        )
    # Visibilidad operativa (ADR-17): el backend `memory` cuenta **por réplica**, así que con
    # varias réplicas el umbral efectivo se multiplica por su número. Se registra al arrancar
    # para que un despliegue multi-réplica advierta que debe usar `redis` si necesita un
    # límite global.
    logger.info(
        "Limitador de login: backend=memory (contador por réplica; usa "
        "RATE_LIMITER_BACKEND=redis para un límite compartido entre réplicas)."
    )
    return InMemorySlidingWindowRateLimiter(
        max_attempts=settings.login_rate_limit_max_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # El esquema se gestiona con migraciones Alembic (`alembic upgrade head`), que el
    # contenedor ejecuta antes de arrancar (ver DECISION_LOG ADR-6). La app ya no crea
    # tablas en el arranque; aquí se administra el motor y, opcionalmente, el worker del
    # outbox. Con `RUN_OUTBOX_WORKER_IN_PROCESS=false` el worker NO se lanza aquí y debe
    # ejecutarse como un proceso dedicado (`python -m app.infrastructure.outbox_worker`),
    # lo que evita el polling redundante de cada réplica de la API. Ver DECISION_LOG ADR-15.
    settings = get_settings()
    engine = get_engine()
    stop_event = asyncio.Event()
    worker: asyncio.Task[None] | None = None
    cleanup: asyncio.Task[None] | None = None
    if settings.run_outbox_worker_in_process:
        worker = asyncio.create_task(run_outbox_worker(stop_event))
    else:
        logger.info(
            "Worker del outbox en proceso desactivado "
            "(RUN_OUTBOX_WORKER_IN_PROCESS=false); ejecútalo como proceso dedicado."
        )
    # Purga periódica de refresh tokens expirados (ADR-21). Comparte el `stop_event` para
    # detenerse limpiamente junto al resto del lifespan.
    if settings.run_token_cleanup_in_process:
        cleanup = asyncio.create_task(
            run_token_cleanup_worker(stop_event, settings.token_cleanup_interval_seconds)
        )
    try:
        yield
    finally:
        stop_event.set()
        if worker is not None:
            await worker
        if cleanup is not None:
            await cleanup
        await engine.dispose()
        redis_client = getattr(app.state, "redis_client", None)
        if redis_client is not None:
            await redis_client.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="Tasks API",
        version="0.1.0",
        description="API de gestión de listas de tareas — Desafío Técnico Backend.",
        lifespan=lifespan,
    )
    # Limitador de login compartido por la app. Vive en `app.state` —no como singleton de
    # módulo— para que cada instancia (incluida cada app de test) tenga su propio estado
    # aislado. Backend seleccionable (memoria/Redis). Ver DECISION_LOG ADR-17.
    app.state.login_rate_limiter = _build_rate_limiter(app, settings)
    # Proxies de confianza para derivar la IP real del cliente (anti-suplantación de XFF).
    app.state.trusted_proxies = parse_trusted_proxies(settings.rate_limit_trusted_proxies)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(lists.router, prefix=API_PREFIX)
    app.include_router(tasks.router, prefix=API_PREFIX)
    return app


app = create_app()
