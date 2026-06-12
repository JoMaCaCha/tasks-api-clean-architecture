"""Tests unitarios de AuthService (registro, login, refresh con rotación, logout)."""

import pytest

from app.application.auth_service import AuthService
from app.domain.exceptions import AuthError, ConflictError
from tests.unit.fakes import (
    FakePasswordHasher,
    FakeRefreshTokenRepository,
    FakeTokenProvider,
    FakeUserRepository,
)


@pytest.fixture
def users() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def refresh_repo() -> FakeRefreshTokenRepository:
    return FakeRefreshTokenRepository()


@pytest.fixture
def service(users: FakeUserRepository, refresh_repo: FakeRefreshTokenRepository) -> AuthService:
    return AuthService(
        user_repository=users,
        password_hasher=FakePasswordHasher(),
        token_provider=FakeTokenProvider(),
        refresh_token_repository=refresh_repo,
        refresh_ttl_days=7,
    )


async def test_register_creates_user(service: AuthService) -> None:
    user = await service.register(email="a@e.com", password="secret123")
    assert user.email == "a@e.com"
    assert user.hashed_password == "hashed::secret123"


async def test_register_duplicate_email_raises(service: AuthService) -> None:
    await service.register(email="a@e.com", password="secret123")
    with pytest.raises(ConflictError):
        await service.register(email="a@e.com", password="other123")


async def test_authenticate_returns_token_pair(service: AuthService) -> None:
    user = await service.register(email="a@e.com", password="secret123")
    access, refresh = await service.authenticate(email="a@e.com", password="secret123")
    assert access == f"token::{user.id}::1"
    assert refresh  # token opaco no vacío


async def test_authenticate_wrong_password_raises(service: AuthService) -> None:
    await service.register(email="a@e.com", password="secret123")
    with pytest.raises(AuthError):
        await service.authenticate(email="a@e.com", password="wrong")


async def test_authenticate_unknown_email_raises(service: AuthService) -> None:
    with pytest.raises(AuthError):
        await service.authenticate(email="ghost@e.com", password="secret123")


async def test_refresh_rotates_and_invalidates_old(service: AuthService) -> None:
    await service.register(email="a@e.com", password="secret123")
    _, refresh1 = await service.authenticate(email="a@e.com", password="secret123")
    _, refresh2 = await service.refresh(refresh1)
    assert refresh2 != refresh1
    # El refresh nuevo funciona...
    await service.refresh(refresh2)


async def test_refresh_reuse_detection_revokes_session(service: AuthService) -> None:
    await service.register(email="a@e.com", password="secret123")
    _, refresh1 = await service.authenticate(email="a@e.com", password="secret123")
    _, refresh2 = await service.refresh(refresh1)  # refresh1 queda revocado

    # Reusar el viejo (robado) dispara la revocación de toda la sesión.
    with pytest.raises(AuthError):
        await service.refresh(refresh1)
    # ...y, como consecuencia, el refresh2 también queda revocado.
    with pytest.raises(AuthError):
        await service.refresh(refresh2)


async def test_refresh_unknown_token_raises(service: AuthService) -> None:
    with pytest.raises(AuthError):
        await service.refresh("inexistente")


async def test_logout_revokes_refresh_token(service: AuthService) -> None:
    await service.register(email="a@e.com", password="secret123")
    _, refresh = await service.authenticate(email="a@e.com", password="secret123")
    await service.logout(refresh)
    with pytest.raises(AuthError):
        await service.refresh(refresh)


async def test_logout_all_bumps_token_version(
    service: AuthService, users: FakeUserRepository
) -> None:
    user = await service.register(email="a@e.com", password="secret123")
    _, refresh = await service.authenticate(email="a@e.com", password="secret123")
    await service.logout_all(user.id)

    refreshed = await users.get(user.id)
    assert refreshed is not None and refreshed.token_version == 2
    # Todos los refresh quedan revocados.
    with pytest.raises(AuthError):
        await service.refresh(refresh)
