"""Traducción centralizada de excepciones de dominio a respuestas HTTP."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.exceptions import DomainError


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # El handler se registra solo para DomainError; el guard es una invariante
    # defensiva que no depende de `assert` (eliminable con `python -O`).
    if not isinstance(exc, DomainError):
        raise exc
    # Un 429 (RateLimitError) puede traer `retry_after`: se expone en la cabecera HTTP
    # estándar `Retry-After` para que el cliente sepa cuándo reintentar.
    retry_after = getattr(exc, "retry_after", None)
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.message}, headers=headers
    )


def register_exception_handlers(app: FastAPI) -> None:
    # Registrar la base cubre todas las subclases vía el MRO de Starlette.
    app.add_exception_handler(DomainError, domain_error_handler)
