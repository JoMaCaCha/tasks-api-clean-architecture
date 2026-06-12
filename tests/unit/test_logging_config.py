"""Tests de la configuración de logging de la aplicación."""

import logging

from app.infrastructure.logging_config import configure_logging


def test_configure_logging_sets_level_for_app_loggers() -> None:
    configure_logging("INFO")
    # Los loggers de la app (p. ej. el del envío simulado) heredan el nivel INFO.
    assert logging.getLogger("app.notifications").getEffectiveLevel() == logging.INFO


def test_configure_logging_is_idempotent() -> None:
    configure_logging("INFO")
    configure_logging("DEBUG")
    app_logger = logging.getLogger("app")
    # No acumula handlers al llamarse varias veces, y aplica el último nivel.
    assert len(app_logger.handlers) == 1
    assert app_logger.level == logging.DEBUG
    assert app_logger.propagate is False
