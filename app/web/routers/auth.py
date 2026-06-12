"""Endpoints de autenticación (bonus §1.b.ii): registro, login, refresh y logout.

Todos los endpoints de `/auth/*` están limitados por IP (ver DECISION_LOG ADR-17): los de
credenciales (`/register`, `/login`, `/refresh`) frenan la fuerza bruta y el barrido de
tokens; `/logout` y `/logout-all` se limitan por consistencia y para acotar el abuso de su
escritura en BD (defensa en profundidad). Al superar el umbral se responde 429.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.application.auth_service import AuthService
from app.domain.security import ILoginRateLimiter
from app.web.client_ip import resolve_client_ip
from app.web.dependencies import CurrentUserDep, get_auth_service, get_login_rate_limiter
from app.web.schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
RateLimiterDep = Annotated[ILoginRateLimiter, Depends(get_login_rate_limiter)]


def _client_key(request: Request, scope: str) -> str:
    """Clave de límite por cliente, resistente a suplantación de `X-Forwarded-For`.

    La IP se deriva con `resolve_client_ip`, que solo honra `XFF` desde proxies de
    confianza (`RATE_LIMIT_TRUSTED_PROXIES`, parseados en `create_app` y guardados en
    `app.state.trusted_proxies`). Sin proxies de confianza usa la IP del socket.
    """
    trusted = request.app.state.trusted_proxies
    return f"{scope}:{resolve_client_ip(request, trusted)}"


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: Request,
    body: RegisterRequest,
    service: AuthServiceDep,
    limiter: RateLimiterDep,
) -> UserResponse:
    await limiter.check(_client_key(request, "register"))
    user = await service.register(email=body.email, password=body.password)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    body: LoginRequest,
    service: AuthServiceDep,
    limiter: RateLimiterDep,
) -> TokenResponse:
    # El límite se aplica ANTES de validar credenciales: así un atacante no puede
    # martillar el endpoint aunque acierte ocasionalmente.
    await limiter.check(_client_key(request, "login"))
    access, refresh = await service.authenticate(email=body.email, password=body.password)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    body: RefreshRequest,
    service: AuthServiceDep,
    limiter: RateLimiterDep,
) -> TokenResponse:
    # `/refresh` también emite tokens y revela validez (200 vs 401), así que es un objetivo
    # de barrido de refresh tokens: se limita por IP igual que login/registro, antes de
    # tocar el token. La rotación single-use mitiga el reúso, pero no el sondeo masivo de
    # tokens candidatos (RFC 9700 §2.13 / OWASP Authentication Cheat Sheet). Ver ADR-17.
    await limiter.check(_client_key(request, "refresh"))
    access, new_refresh = await service.refresh(body.refresh_token)
    return TokenResponse(access_token=access, refresh_token=new_refresh)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    body: LogoutRequest,
    service: AuthServiceDep,
    limiter: RateLimiterDep,
) -> None:
    """Revoca un refresh token concreto (cierre de esta sesión)."""
    # `/logout` es público y dispara una escritura en BD (revocación por hash); se limita por
    # IP igual que el resto de `/auth/*` para acotar el abuso. Ver ADR-17.
    await limiter.check(_client_key(request, "logout"))
    await service.logout(body.refresh_token)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    request: Request,
    service: AuthServiceDep,
    user: CurrentUserDep,
    limiter: RateLimiterDep,
) -> None:
    """Invalida todos los access vigentes y revoca todos los refresh del usuario."""
    await limiter.check(_client_key(request, "logout"))
    await service.logout_all(user.id)
