"""Excepciones de negocio del dominio.

Cada excepción transporta el código HTTP con el que la capa `web` la traduce, de modo
que el dominio permanece agnóstico de FastAPI pero el mapeo queda centralizado.
"""


class DomainError(Exception):
    """Excepción base del dominio. Por defecto se traduce a 400 Bad Request.

    ``commit_side_effects`` controla qué hace la unidad de trabajo de la request al
    propagarse este error (ver ``app/infrastructure/db/session.py``). El **default es
    seguro**: ``False`` → se revierte cualquier escritura previa. Solo un caso de uso que
    escribe *deliberadamente* antes de rechazar (p. ej. revocar una sesión al detectar
    reúso de un refresh token) debe lanzar con ``commit_side_effects=True`` para persistir
    ese efecto. Así, añadir un nuevo error de validación no puede filtrar escrituras por
    accidente: la persistencia es una decisión explícita, no la conducta por defecto.
    """

    status_code: int = 400

    def __init__(self, message: str, *, commit_side_effects: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.commit_side_effects = commit_side_effects


class NotFoundError(DomainError):
    """Un recurso solicitado no existe."""

    status_code = 404


class ConflictError(DomainError):
    """La operación choca con el estado actual (p. ej. email duplicado)."""

    status_code = 409


class AuthError(DomainError):
    """Credenciales inválidas o token ausente/expirado."""

    status_code = 401


class ForbiddenError(DomainError):
    """El usuario está autenticado pero no tiene permiso sobre el recurso."""

    status_code = 403


class PreconditionRequiredError(DomainError):
    """Falta una precondición obligatoria para una escritura condicional.

    La emiten las mutaciones de una tarea cuando la petición no trae la cabecera `If-Match`
    con el ETag de versión: el servidor exige la condición para evitar el *lost update*
    (RFC 6585 §3 define 428 justo para este caso). Ver DECISION_LOG ADR-26.
    """

    status_code = 428


class PreconditionFailedError(DomainError):
    """El estado del recurso ya no coincide con la versión que el cliente esperaba.

    Se lanza cuando el ETag de `If-Match` no corresponde a la versión actual de la tarea
    (alguien la modificó entre la lectura y la escritura): control de concurrencia optimista
    con 412 Precondition Failed (RFC 9110). Ver DECISION_LOG ADR-26.
    """

    status_code = 412


class RateLimitError(DomainError):
    """Se superó el límite de intentos permitido (p. ej. brute-force en el login).

    Lleva un ``retry_after`` opcional (segundos) que la capa `web` expone en la cabecera
    HTTP ``Retry-After`` de la respuesta 429, como recomienda el estándar.
    """

    status_code = 429

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
