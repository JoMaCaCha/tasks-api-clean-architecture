"""Inyección de dependencias de la capa web (cableado con `Depends`).

Aquí se construyen repositorios, proveedores de seguridad y servicios a partir de una
sesión de base de datos, y se resuelve el usuario autenticado desde el JWT.
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth_service import AuthService
from app.application.task_list_service import TaskListService
from app.application.task_service import TaskService
from app.domain.entities import User
from app.domain.exceptions import AuthError
from app.domain.repositories import (
    IListMemberRepository,
    IOutboxRepository,
    IRefreshTokenRepository,
    ITaskListRepository,
    ITaskRepository,
    IUserRepository,
)
from app.domain.security import ILoginRateLimiter, IPasswordHasher, ITokenProvider
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.db.repositories import (
    SqlAlchemyListMemberRepository,
    SqlAlchemyOutboxRepository,
    SqlAlchemyRefreshTokenRepository,
    SqlAlchemyTaskListRepository,
    SqlAlchemyTaskRepository,
    SqlAlchemyUserRepository,
)
from app.infrastructure.db.session import get_session
from app.infrastructure.security.jwt_provider import JwtTokenProvider
from app.infrastructure.security.password_hasher import BcryptPasswordHasher

_bearer_scheme = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# --- Repositorios ------------------------------------------------------------


def get_task_list_repository(session: SessionDep) -> ITaskListRepository:
    return SqlAlchemyTaskListRepository(session)


def get_task_repository(session: SessionDep) -> ITaskRepository:
    return SqlAlchemyTaskRepository(session)


def get_user_repository(session: SessionDep) -> IUserRepository:
    return SqlAlchemyUserRepository(session)


def get_list_member_repository(session: SessionDep) -> IListMemberRepository:
    return SqlAlchemyListMemberRepository(session)


def get_refresh_token_repository(session: SessionDep) -> IRefreshTokenRepository:
    return SqlAlchemyRefreshTokenRepository(session)


def get_outbox_repository(session: SessionDep) -> IOutboxRepository:
    return SqlAlchemyOutboxRepository(session)


# --- Seguridad y notificaciones ---------------------------------------------


def get_password_hasher() -> IPasswordHasher:
    return BcryptPasswordHasher()


def get_token_provider(settings: SettingsDep) -> ITokenProvider:
    return JwtTokenProvider(
        secret_key=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        expire_minutes=settings.access_token_expire_minutes,
    )


def get_login_rate_limiter(request: Request) -> ILoginRateLimiter:
    """Devuelve el limitador compartido de la app (creado en `create_app`)."""
    limiter: ILoginRateLimiter = request.app.state.login_rate_limiter
    return limiter


# --- Servicios ---------------------------------------------------------------


def get_task_list_service(
    repository: Annotated[ITaskListRepository, Depends(get_task_list_repository)],
    member_repository: Annotated[IListMemberRepository, Depends(get_list_member_repository)],
    user_repository: Annotated[IUserRepository, Depends(get_user_repository)],
    task_repository: Annotated[ITaskRepository, Depends(get_task_repository)],
) -> TaskListService:
    return TaskListService(
        repository=repository,
        member_repository=member_repository,
        user_repository=user_repository,
        task_repository=task_repository,
    )


def get_task_service(
    task_repository: Annotated[ITaskRepository, Depends(get_task_repository)],
    member_repository: Annotated[IListMemberRepository, Depends(get_list_member_repository)],
    user_repository: Annotated[IUserRepository, Depends(get_user_repository)],
) -> TaskService:
    return TaskService(
        task_repository=task_repository,
        member_repository=member_repository,
        user_repository=user_repository,
    )


def get_auth_service(
    user_repository: Annotated[IUserRepository, Depends(get_user_repository)],
    password_hasher: Annotated[IPasswordHasher, Depends(get_password_hasher)],
    token_provider: Annotated[ITokenProvider, Depends(get_token_provider)],
    refresh_repository: Annotated[IRefreshTokenRepository, Depends(get_refresh_token_repository)],
    settings: SettingsDep,
) -> AuthService:
    return AuthService(
        user_repository=user_repository,
        password_hasher=password_hasher,
        token_provider=token_provider,
        refresh_token_repository=refresh_repository,
        refresh_ttl_days=settings.refresh_token_expire_days,
    )


# --- Usuario autenticado -----------------------------------------------------


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    token_provider: Annotated[ITokenProvider, Depends(get_token_provider)],
    user_repository: Annotated[IUserRepository, Depends(get_user_repository)],
) -> User:
    if credentials is None:
        raise AuthError("No autenticado")
    claims = token_provider.decode(credentials.credentials)
    user = await user_repository.get(int(claims.subject))
    if user is None:
        raise AuthError("Usuario del token inexistente")
    # Invalida tokens emitidos antes de un logout global o cambio de contraseña.
    if user.token_version != claims.token_version:
        raise AuthError("Token revocado; vuelve a iniciar sesión")
    return user


# Usuario autenticado, inyectable en los endpoints que necesitan su identidad (p. ej.
# para acotar los recursos a su `owner_id`).
CurrentUserDep = Annotated[User, Depends(get_current_user)]
