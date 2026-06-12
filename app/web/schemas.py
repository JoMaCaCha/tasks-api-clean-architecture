"""Esquemas de request/response de la API (Pydantic V2).

Separados de las entidades de dominio: la API nunca expone `hashed_password` ni acopla
su contrato a la persistencia.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.domain.enums import ListRole, TaskPriority, TaskStatus

# bcrypt solo procesa los primeros 72 **bytes** del input; el resto se ignora. Para no
# truncar contraseñas en silencio (un sufijo descartado deja dos contraseñas distintas con
# el mismo hash), se rechaza en el límite de validación toda contraseña que exceda ese
# tamaño en bytes UTF-8. Es la recomendación primaria de OWASP (Password Storage Cheat
# Sheet, "enforce a maximum password length of 72 bytes"); se prefiere a pre-hashear con
# SHA-256, que sin un pepper en HMAC abre la puerta al *password shucking*. Ver ADR-22.
_MAX_PASSWORD_BYTES = 72

# --- Auth --------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    # `max_length` acota el input en caracteres (guarda barata anti-DoS antes de codificar);
    # el validador de bytes de abajo fija el contrato preciso de bcrypt (72 bytes UTF-8).
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _within_bcrypt_byte_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > _MAX_PASSWORD_BYTES:
            raise ValueError(
                f"La contraseña excede los {_MAX_PASSWORD_BYTES} bytes que bcrypt procesa; "
                "acórtala (los bytes sobrantes se ignorarían y debilitarían el hash)."
            )
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    created_at: datetime


# --- Listas ------------------------------------------------------------------


class TaskListCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class TaskListUpdate(TaskListCreate):
    pass


class TaskListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    title: str
    description: str | None
    created_at: datetime


class TaskListDetailResponse(TaskListResponse):
    completion_percentage: float


class TaskListPage(BaseModel):
    """Página de listas con cursor de keyset (`next_cursor` = id del último elemento)."""

    items: list[TaskListResponse]
    limit: int
    next_cursor: int | None


# --- Colaboradores -----------------------------------------------------------


class ListMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    role: ListRole


class ListMemberPage(BaseModel):
    """Página de colaboradores con cursor de keyset (`next_cursor` = `user_id` del último)."""

    members: list[ListMemberResponse]
    limit: int
    next_cursor: int | None


class AddMemberRequest(BaseModel):
    user_id: int
    role: ListRole = ListRole.VIEWER


class UpdateMemberRoleRequest(BaseModel):
    role: ListRole


# --- Tareas ------------------------------------------------------------------


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    assignee_id: int | None = None


class TaskUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    status: TaskStatus
    priority: TaskPriority
    assignee_id: int | None = None


class TaskStatusUpdate(BaseModel):
    status: TaskStatus


class TaskAssigneeUpdate(BaseModel):
    assignee_id: int


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    list_id: int
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    assignee_id: int | None
    # Versión para concurrencia optimista; se refleja además en la cabecera `ETag` de las
    # respuestas individuales. El cliente la reenvía en `If-Match` al modificar (ver ADR-26).
    version: int
    created_at: datetime


class TaskCollectionResponse(BaseModel):
    """Listado paginado de tareas con el campo extra de completitud (§1.a.iv).

    `completion_percentage` se calcula sobre **todas** las tareas de la lista, no solo
    sobre la página devuelta.
    """

    list_id: int
    completion_percentage: float
    limit: int
    next_cursor: int | None
    tasks: list[TaskResponse]
