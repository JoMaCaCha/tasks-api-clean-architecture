"""Tests del guard de configuración (ADR-5): el secreto de ejemplo no llega a producción.

`Settings` se instancia con kwargs explícitos, que tienen prioridad sobre el entorno y el
`.env`, de modo que la prueba es hermética e independiente de variables externas.
"""

import pytest
from pydantic import ValidationError

from app.infrastructure.config import (
    _MIN_PRODUCTION_SECRET_ENTROPY_BITS,
    Settings,
    _estimate_secret_entropy_bits,
)

# El mismo secreto versionado en `.env.example` (conocido y público).
_EXAMPLE_SECRET = "dev-only-INSECURE-secret-change-before-deploy-0123456789"
_CUSTOM_SECRET = "una-clave-propia-de-mas-de-treinta-y-dos-caracteres"
# Secreto fuerte y aleatorio (alta diversidad de caracteres): supera el piso de entropía.
_STRONG_SECRET = "Zk7pQ2mX9vL4wR8nB3hC6yD1gF5sJ0aE-tU2iO4Pq8Wd"
# Secreto débil de relleno: ≥ 32 caracteres pero de baja entropía (una palabra repetida).
_WEAK_SECRET = "changeme" * 5


def test_example_secret_allowed_in_dev() -> None:
    settings = Settings(app_env="dev", jwt_secret_key=_EXAMPLE_SECRET)
    assert settings.jwt_secret_key == _EXAMPLE_SECRET


@pytest.mark.parametrize("env", ["development", "local", "test"])
def test_example_secret_allowed_in_other_dev_aliases(env: str) -> None:
    # development/local/test también se consideran entornos de desarrollo.
    assert Settings(app_env=env, jwt_secret_key=_EXAMPLE_SECRET).app_env == env


def test_example_secret_rejected_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production", jwt_secret_key=_EXAMPLE_SECRET)


def test_custom_secret_allowed_in_production() -> None:
    settings = Settings(app_env="production", jwt_secret_key=_CUSTOM_SECRET)
    assert settings.app_env == "production" and settings.jwt_secret_key == _CUSTOM_SECRET


def test_strong_secret_allowed_in_production() -> None:
    # Un secreto aleatorio de alta entropía es válido en producción (ADR-25).
    settings = Settings(app_env="production", jwt_secret_key=_STRONG_SECRET)
    assert settings.jwt_secret_key == _STRONG_SECRET


def test_weak_low_entropy_secret_rejected_in_production() -> None:
    # ≥ 32 caracteres pero de baja entropía (palabra repetida): se rechaza fuera de dev (ADR-25).
    with pytest.raises(ValidationError):
        Settings(app_env="production", jwt_secret_key=_WEAK_SECRET)


def test_weak_secret_tolerated_in_dev() -> None:
    # En desarrollo no se exige el piso de entropía (arranque local sin fricción).
    assert Settings(app_env="dev", jwt_secret_key=_WEAK_SECRET).jwt_secret_key == _WEAK_SECRET


def test_entropy_estimate_orders_weak_below_strong() -> None:
    # El piso separa de forma fiable un secreto débil de uno fuerte.
    assert _estimate_secret_entropy_bits(_WEAK_SECRET) < _MIN_PRODUCTION_SECRET_ENTROPY_BITS
    assert _estimate_secret_entropy_bits(_STRONG_SECRET) >= _MIN_PRODUCTION_SECRET_ENTROPY_BITS
    assert _estimate_secret_entropy_bits("") == 0.0
