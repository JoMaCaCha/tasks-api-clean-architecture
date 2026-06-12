"""Puertos de seguridad.

Abstracciones para hashing de contraseñas y emisión/verificación de tokens. Viven en
`domain` (son solo interfaces, sin dependencias de infraestructura) para que
`application` dependa de ellas y no de implementaciones concretas.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AccessTokenClaims:
    """Datos verificados que transporta un access token."""

    subject: str
    token_version: int


class IPasswordHasher(ABC):
    """Hashing y verificación de contraseñas."""

    @abstractmethod
    def hash(self, plain: str) -> str: ...

    @abstractmethod
    def verify(self, plain: str, hashed: str) -> bool: ...


class ITokenProvider(ABC):
    """Emisión y verificación de access tokens (JWT)."""

    @abstractmethod
    def create_access_token(self, *, subject: str, token_version: int) -> str: ...

    @abstractmethod
    def decode(self, token: str) -> AccessTokenClaims:
        """Devuelve los claims del token o lanza ``AuthError`` si es inválido."""
        ...


class ILoginRateLimiter(ABC):
    """Limitador de intentos para endpoints sensibles (login/registro).

    Protege contra fuerza bruta acotando el número de intentos por clave (p. ej. IP) en
    una ventana de tiempo. Es un puerto: la implementación concreta (en memoria, Redis,
    etc.) vive en `infrastructure`. Ver DECISION_LOG ADR-17.
    """

    @abstractmethod
    async def check(self, key: str) -> None:
        """Registra un intento para ``key`` y lanza ``RateLimitError`` si excede el umbral."""
        ...
