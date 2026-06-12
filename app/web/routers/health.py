"""Healthchecks públicos: liveness (sin dependencias) y readiness (chequea BD).

Ver DECISION_LOG ADR-10: la liveness no toca la BD para no reiniciar el contenedor
ante cortes transitorios; la readiness sí, para retirar la instancia del balanceador.
"""

import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.web.dependencies import SessionDep

logger = logging.getLogger("app.health")

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: el proceso responde. No depende de la BD."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(session: SessionDep) -> JSONResponse:
    """Readiness: comprueba la conectividad con la base de datos (`SELECT 1`)."""
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        # No se traga el error: se registra (con traza) para diagnosticar por qué la
        # BD no responde, y se reporta 503 para que el orquestador retire la instancia.
        # Se revierte la transacción fallida para que la unidad de trabajo de la request
        # (`get_session`) no intente luego un commit sobre una sesión inválida.
        await session.rollback()
        logger.warning("Readiness check fallido: la base de datos no responde", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable"},
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ready"})
