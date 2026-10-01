"""Casos de uso de autenticación con refresh tokens.

Flujo: `login`/`register` → un **access token** corto (JWT, stateless) y un **refresh
token** opaco de vida larga (se persiste solo su hash). `refresh` aplica **rotación
single-use** (revoca el anterior y emite uno nuevo) con **detección de reutilización**:
si llega un refresh ya revocado, se revoca toda la sesión del usuario. `logout` revoca un
refresh concreto; `logout_all` incrementa la versión de token (invalida todos los access
vigentes) y revoca todos los refresh. Ver DECISION_LOG ADR-13.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from app.domain.entities import User
from app.domain.exceptions import AuthError, ConflictError
from app.domain.repositories import IRefreshTokenRepository, IUserRepository
from app.domain.security import IPasswordHasher, ITokenProvider


class AuthService:
    """Registro, login, rotación de refresh tokens y revocación."""

    def __init__(
        self,
        *,
        user_repository: IUserRepository,
        password_hasher: IPasswordHasher,
        token_provider: ITokenProvider,
        refresh_token_repository: IRefreshTokenRepository,
        refresh_ttl_days: int,
    ) -> None:
        self._users = user_repository
        self._hasher = password_hasher
        self._tokens = token_provider
        self._refresh = refresh_token_repository
        self._refresh_ttl = timedelta(days=refresh_ttl_days)

    # -- helpers ---------------------------------------------------------------
    @staticmethod
    def _hash(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_expired(expires_at: datetime) -> bool:
        exp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        return exp <= datetime.now(UTC)

    async def _issue_refresh(self, user_id: int) -> str:
        raw = secrets.token_urlsafe(48)
        await self._refresh.add(
            user_id=user_id,
            token_hash=self._hash(raw),
            expires_at=datetime.now(UTC) + self._refresh_ttl,
        )
        return raw

    def _issue_access(self, user: User) -> str:
        return self._tokens.create_access_token(
            subject=str(user.id), token_version=user.token_version
        )

    # -- casos de uso ----------------------------------------------------------
    async def register(self, *, email: str, password: str) -> User:
        if await self._users.get_by_email(email) is not None:
            raise ConflictError(f"El email {email} ya está registrado")
        hashed = self._hasher.hash(password)
        return await self._users.create(email=email, hashed_password=hashed)

    async def authenticate(self, *, email: str, password: str) -> tuple[str, str]:
        """Valida credenciales y devuelve ``(access_token, refresh_token)``."""
        user = await self._users.get_by_email(email)
        if user is None or not self._hasher.verify(password, user.hashed_password):
            raise AuthError("Credenciales inválidas")
        return self._issue_access(user), await self._issue_refresh(user.id)

    async def refresh(self, raw_refresh_token: str) -> tuple[str, str]:
        """Rota el refresh token y devuelve un par nuevo ``(access, refresh)``."""
        token_hash = self._hash(raw_refresh_token)
        record = await self._refresh.get_by_hash(token_hash)
        if record is None:
            raise AuthError("Refresh token inválido")
        if record.revoked:
            # Reutilización de un token ya rotado: posible robo. Revoca toda la sesión.
            # `commit_side_effects=True`: la revocación es un efecto deliberado que DEBE
            # persistir aunque se rechace la petición (la unidad de trabajo, por defecto,
            # revierte ante un DomainError). Ver app/infrastructure/db/session.py.
            await self._refresh.revoke_all_for_user(record.user_id)
            raise AuthError("Refresh token reutilizado; sesión revocada", commit_side_effects=True)
        if self._is_expired(record.expires_at):
            raise AuthError("Refresh token expirado")
        user = await self._users.get(record.user_id)
        if user is None:
            raise AuthError("Usuario del token inexistente")
        await self._refresh.revoke(token_hash)
        return self._issue_access(user), await self._issue_refresh(user.id)

    async def logout(self, raw_refresh_token: str) -> None:
        """Revoca un refresh token concreto (cierre de una sesión)."""
        await self._refresh.revoke(self._hash(raw_refresh_token))

    async def logout_all(self, user_id: int) -> None:
        """Invalida todos los access vigentes y revoca todos los refresh del usuario."""
        await self._users.bump_token_version(user_id)
        await self._refresh.revoke_all_for_user(user_id)
