"""Tests unitarios de las implementaciones de seguridad (bcrypt y JWT)."""

import jwt
import pytest

from app.domain.exceptions import AuthError
from app.infrastructure.security.jwt_provider import JwtTokenProvider
from app.infrastructure.security.password_hasher import BcryptPasswordHasher


def test_bcrypt_hash_and_verify() -> None:
    hasher = BcryptPasswordHasher()
    hashed = hasher.hash("secret123")
    assert hashed != "secret123"
    assert hasher.verify("secret123", hashed) is True
    assert hasher.verify("wrong", hashed) is False


def test_bcrypt_backstop_handles_input_over_72_bytes() -> None:
    # El contrato (rechazar >72 bytes) se aplica en la validación de `RegisterRequest`
    # (ADR-22); este recorte defensivo garantiza que, aun si el hasher recibiera un input
    # más largo, no lanza ni depende de la conducta de truncado de la librería bcrypt.
    hasher = BcryptPasswordHasher()
    hashed = hasher.hash("x" * 100)
    assert hasher.verify("x" * 100, hashed) is True


# Secretos de prueba de ≥32 caracteres (como exige la config real), evita avisos de PyJWT.
_SECRET = "test-secret-key-with-at-least-32-chars"
_OTHER_SECRET = "another-secret-key-with-32-plus-characters"


def test_jwt_roundtrip_carries_subject_and_version() -> None:
    provider = JwtTokenProvider(secret_key=_SECRET, algorithm="HS256", expire_minutes=5)
    token = provider.create_access_token(subject="42", token_version=3)
    claims = provider.decode(token)
    assert claims.subject == "42"
    assert claims.token_version == 3


def test_jwt_invalid_token_raises() -> None:
    provider = JwtTokenProvider(secret_key=_SECRET, algorithm="HS256", expire_minutes=5)
    with pytest.raises(AuthError):
        provider.decode("not-a-token")


def test_jwt_wrong_secret_raises() -> None:
    issuer = JwtTokenProvider(secret_key=_SECRET, algorithm="HS256", expire_minutes=5)
    verifier = JwtTokenProvider(secret_key=_OTHER_SECRET, algorithm="HS256", expire_minutes=5)
    token = issuer.create_access_token(subject="1", token_version=1)
    with pytest.raises(AuthError):
        verifier.decode(token)


def test_jwt_token_without_sub_raises() -> None:
    # Token bien firmado pero sin claim `sub`: debe rechazarse explícitamente.
    token = jwt.encode({"type": "access", "ver": 1}, _SECRET, algorithm="HS256")
    provider = JwtTokenProvider(secret_key=_SECRET, algorithm="HS256", expire_minutes=5)
    with pytest.raises(AuthError):
        provider.decode(token)


def test_jwt_wrong_type_rejected() -> None:
    # Un token sin `type=access` (p. ej. otro propósito) no debe autenticar.
    token = jwt.encode({"sub": "1", "ver": 1, "type": "other"}, _SECRET, algorithm="HS256")
    provider = JwtTokenProvider(secret_key=_SECRET, algorithm="HS256", expire_minutes=5)
    with pytest.raises(AuthError):
        provider.decode(token)
