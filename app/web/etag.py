"""Soporte de `ETag` / `If-Match` para escrituras condicionales (concurrencia optimista).

Cada tarea expone su versión como un **ETag fuerte** (`"<version>"`). Las mutaciones de una
tarea existente exigen la precondición `If-Match` con esa versión:

- si la cabecera **falta** → 428 Precondition Required (RFC 6585 §3, definido justo para el
  problema de *lost update*: leer estado, modificarlo y reescribir pisando un cambio ajeno);
- si **no coincide** con la versión actual → 412 Precondition Failed (RFC 9110).

El comodín ``If-Match: *`` se acepta como "cualquier versión vigente" (la existencia de la
tarea ya la garantiza la autorización). Ver DECISION_LOG ADR-26.
"""

from app.domain.exceptions import PreconditionFailedError, PreconditionRequiredError


def make_etag(version: int) -> str:
    """ETag fuerte a partir de la versión de la tarea."""
    return f'"{version}"'


def require_if_match(if_match: str | None) -> int | None:
    """Versión esperada por el cliente, o ``None`` para el comodín ``*``.

    Lanza ``PreconditionRequiredError`` (428) si la cabecera falta o está vacía, y
    ``PreconditionFailedError`` (412) si está presente pero mal formada (no puede
    corresponder a ninguna versión real, p. ej. un ETag no numérico).
    """
    if if_match is None or not if_match.strip():
        raise PreconditionRequiredError(
            "Se requiere la cabecera If-Match con el ETag de la última lectura de la tarea "
            "para modificarla y evitar sobrescribir cambios concurrentes."
        )
    token = if_match.strip()
    if token == "*":
        return None  # comodín: vale cualquier versión vigente
    # `If-Match` usa comparación fuerte (RFC 9110): un ETag débil (`W/"x"`) no debe coincidir.
    if token.startswith("W/"):
        raise PreconditionFailedError(
            "If-Match no admite ETags débiles (W/...) para escrituras condicionales."
        )
    unquoted = token.strip('"')
    try:
        return int(unquoted)
    except ValueError as exc:
        raise PreconditionFailedError(
            "La cabecera If-Match no es un ETag de versión válido."
        ) from exc
