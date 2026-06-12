"""Implementación de emisión/verificación de access tokens (JWT) con PyJWT."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt

from app.domain.exceptions import AuthError
from app.domain.security import AccessTokenClaims, ITokenProvider


class JwtTokenProvider(ITokenProvider):
    def __init__(self, *, secret_key: str, algorithm: str, expire_minutes: int) -> None:
        self._secret_key = secret_key
        self._algorithm = algorithm
        self._expire_minutes = expire_minutes

    def create_access_token(self, *, subject: str, token_version: int) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": subject,
            "ver": token_version,
            "type": "access",
            "jti": uuid4().hex,
            "iat": now,
            "exp": now + timedelta(minutes=self._expire_minutes),
        }
        return jwt.encode(payload, self._secret_key, algorithm=self._algorithm)

    def decode(self, token: str) -> AccessTokenClaims:
        try:
            payload = jwt.decode(token, self._secret_key, algorithms=[self._algorithm])
        except jwt.PyJWTError as exc:
            raise AuthError("Token inválido o expirado") from exc
        if payload.get("type") != "access":
            raise AuthError("Tipo de token no válido")
        subject = payload.get("sub")
        version = payload.get("ver")
        if subject is None or version is None:
            raise AuthError("Token incompleto")
        return AccessTokenClaims(subject=str(subject), token_version=int(version))
