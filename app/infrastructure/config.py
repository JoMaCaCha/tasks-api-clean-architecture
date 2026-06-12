"""Configuración de la aplicación (pydantic-settings).

Lee variables de entorno (o un archivo `.env`). El `DATABASE_URL` trae un default de
conveniencia para desarrollo, pero `JWT_SECRET_KEY` es **obligatorio** y sin valor por
defecto: la app falla al arrancar si no se provee (patrón fail-fast), evitando firmar
tokens con un secreto conocido. Ver DECISION_LOG ADR-5.
"""

import math
from collections import Counter
from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Entornos considerados "de desarrollo": en ellos se tolera el secreto de ejemplo del
# `.env.example` (arranque local en dos comandos). Cualquier otro valor de `APP_ENV` se
# trata como productivo y activa el guard de secreto inseguro.
_DEV_ENVIRONMENTS = frozenset({"dev", "development", "local", "test"})

# Secretos de ejemplo/desarrollo conocidos (versionados en `.env.example`). Nunca deben
# usarse fuera de desarrollo; el guard de abajo impide que lleguen a producción (fail-fast).
_KNOWN_INSECURE_JWT_SECRETS = frozenset(
    {"dev-only-INSECURE-secret-change-before-deploy-0123456789"}
)

# Piso de entropía (en bits) exigido al `JWT_SECRET_KEY` fuera de desarrollo. RFC 7518 §3.2
# obliga a una clave de al menos 256 bits de **longitud** para HS256 (cubierto por
# `min_length=32` caracteres ≥ 32 bytes); pero la longitud sola no basta: OWASP advierte que
# "una clave larga con baja entropía es menos segura que una corta con alta entropía", y un
# secreto HMAC débil se rompe por fuerza bruta en segundos. 112 bits es el nivel de seguridad
# simétrica "aceptable" de NIST SP 800-57 (margen hasta ~2030); un secreto generado al azar
# (p. ej. `secrets.token_urlsafe(48)`) lo supera con holgura, mientras que uno débil o de
# relleno (una palabra repetida, caracteres repetidos) queda por debajo. Esto generaliza el
# rechazo: ya no depende de una lista fija de secretos conocidos. Ver DECISION_LOG ADR-25.
_MIN_PRODUCTION_SECRET_ENTROPY_BITS = 112.0


def _estimate_secret_entropy_bits(secret: str) -> float:
    """Estima la entropía de Shannon (en bits) de ``secret`` por su diversidad de caracteres.

    ``H = -Σ p(c)·log2 p(c)`` por carácter, multiplicado por la longitud. Es una **heurística**
    (asume símbolos i.i.d.; no modela patrones secuenciales como ``abcabc``), pero distingue de
    forma fiable un secreto aleatorio —entropía alta— de uno débil o de relleno —entropía baja—,
    que es justo la clase que se quiere rechazar en producción. Ver DECISION_LOG ADR-25.
    """
    if not secret:
        return 0.0
    length = len(secret)
    return (
        -sum((count / length) * math.log2(count / length) for count in Counter(secret).values())
        * length
    )


class Settings(BaseSettings):
    """Parámetros de configuración tipados."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Entorno de ejecución. Default `dev` para no estorbar el arranque local ni los tests;
    # en producción debe fijarse a `production` (u otro valor no-dev) para activar el guard
    # que rechaza el secreto de ejemplo. Ver DECISION_LOG ADR-5.
    app_env: str = "dev"

    database_url: str = "postgresql+asyncpg://crehana:crehana@db:5432/crehana_tasks"

    # Requerido (sin default) y de al menos 32 caracteres: si falta o es débil,
    # pydantic-settings lanza ValidationError al instanciarse.
    jwt_secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    # Access token corto (OWASP: 5–15 min) + refresh token de vida larga con rotación.
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Límite de intentos por IP en /auth/login y /auth/register (defensa frente a fuerza
    # bruta). 0 deshabilita el límite. Ver DECISION_LOG ADR-17.
    login_rate_limit_max_attempts: int = 10
    login_rate_limit_window_seconds: int = 60
    # Backend del límite: "memory" (por réplica, default) o "redis" (compartido entre
    # réplicas). Con "redis" se usa `redis_url`. Ver DECISION_LOG ADR-17.
    rate_limiter_backend: str = "memory"
    redis_url: str = "redis://localhost:6379/0"
    # Proxies de confianza (CSV de IPs/CIDR) cuyo `X-Forwarded-For` se honra al derivar la
    # IP del cliente. Vacío = no confiar en XFF (usar la IP del socket), seguro por defecto.
    rate_limit_trusted_proxies: str = ""

    # Nivel de log de la aplicación (DEBUG/INFO/WARNING/...). El envío simulado de email
    # (§1.b.iv) y la entrega del outbox se registran en INFO.
    log_level: str = "INFO"

    # Worker del outbox: por defecto se ejecuta dentro del proceso de la API (en el
    # `lifespan`), suficiente para 1..pocas réplicas. En despliegues con muchas réplicas
    # conviene desactivarlo aquí (`RUN_OUTBOX_WORKER_IN_PROCESS=false`) y lanzar el worker
    # como un proceso dedicado (`python -m app.infrastructure.outbox_worker`). El claim con
    # `FOR UPDATE SKIP LOCKED` hace segura cualquier combinación. Ver DECISION_LOG ADR-15.
    run_outbox_worker_in_process: bool = True

    # Purga periódica de refresh tokens expirados (anti-crecimiento de la tabla). Corre en
    # el proceso de la API por defecto; con muchas réplicas puede desactivarse aquí y
    # ejecutarse como tarea programada externa (la purga es idempotente). Ver ADR-21.
    run_token_cleanup_in_process: bool = True
    token_cleanup_interval_seconds: int = 3600

    # Backend de notificaciones: "log" (simulado) o "smtp" (envío real). Ver ADR-9.
    notifier_backend: str = "log"
    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "no-reply@crehana-tasks.local"
    smtp_use_tls: bool = False

    @model_validator(mode="after")
    def _enforce_production_secret_strength(self) -> Self:
        """Fail-fast: en producción, exige un `JWT_SECRET_KEY` fuerte (no de ejemplo ni débil).

        El `.env.example` versiona un `JWT_SECRET_KEY` de conveniencia para el arranque local.
        Sin este guard, copiarlo a producción tal cual firmaría tokens con un secreto público
        y conocido. Siguiendo la guía de seguridad (no dejar que defaults inseguros lleguen a
        producción), fuera de un entorno de desarrollo la app **no arranca** si:

        1. el secreto es uno de ejemplo **conocido** (lista versionada), o
        2. su **entropía estimada** es insuficiente (< ``_MIN_PRODUCTION_SECRET_ENTROPY_BITS``),
           lo que delata un secreto débil o de relleno aunque tenga ≥ 32 caracteres.

        El punto (2) generaliza el control: ya no depende de una lista fija, sino que rechaza
        cualquier secreto de baja entropía (RFC 7518 §3.2 exige ≥ 256 bits de longitud para
        HS256, pero la longitud no garantiza fortaleza). Ver DECISION_LOG ADR-5 y ADR-25.
        """
        if self.app_env.lower() in _DEV_ENVIRONMENTS:
            return self
        if self.jwt_secret_key in _KNOWN_INSECURE_JWT_SECRETS:
            raise ValueError(
                "JWT_SECRET_KEY es un secreto de ejemplo/desarrollo y APP_ENV no es de "
                "desarrollo. Genera una clave propia (p. ej. "
                '`python -c "import secrets; print(secrets.token_urlsafe(48))"`) e '
                "inyéctala desde un gestor de secretos antes de desplegar."
            )
        if _estimate_secret_entropy_bits(self.jwt_secret_key) < _MIN_PRODUCTION_SECRET_ENTROPY_BITS:
            raise ValueError(
                "JWT_SECRET_KEY tiene entropía insuficiente para producción "
                f"(< {int(_MIN_PRODUCTION_SECRET_ENTROPY_BITS)} bits estimados): parece débil o "
                "de relleno (una palabra o caracteres repetidos). Genera uno aleatorio, p. ej. "
                '`python -c "import secrets; print(secrets.token_urlsafe(48))"`, e inyéctalo '
                "desde un gestor de secretos."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia cacheada de `Settings`."""
    return Settings()
