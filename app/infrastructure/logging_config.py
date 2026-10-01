"""Configuración de logging de la aplicación.

Los loggers de la app se nombran bajo el prefijo ``app.*`` (p. ej. ``app.notifications``,
``app.outbox``). Por defecto, uvicorn solo configura sus propios loggers, así que sin esto
los mensajes de la app (como el "envío simulado" de email) no se verían. Aquí
se configura el logger raíz ``app`` con un handler propio y el nivel deseado.
"""

import logging

_APP_LOGGER = "app"
_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configura el logger ``app`` (idempotente: reemplaza sus handlers en cada llamada)."""
    logger = logging.getLogger(_APP_LOGGER)
    logger.setLevel(level.upper())
    # Evita acumular handlers si se llama varias veces (p. ej. al crear varias apps en tests).
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
    # No propaga al root para no duplicar líneas con la config de uvicorn.
    logger.propagate = False
