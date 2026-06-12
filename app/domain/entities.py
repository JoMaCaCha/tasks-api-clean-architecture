"""Entidades del dominio (Pydantic V2).

Representan los objetos de negocio ya persistidos (con `id`). No dependen de
SQLAlchemy ni de FastAPI; `from_attributes=True` permite construirlas desde modelos
ORM sin acoplar el dominio a ellos.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.domain.enums import ListRole, OutboxStatus, TaskPriority, TaskStatus


class User(BaseModel):
    """Usuario del sistema (necesario para JWT y asignación de tareas)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    hashed_password: str
    # Se incrementa para invalidar de golpe todos los access token vigentes del usuario
    # (logout global, cambio de contraseña). El JWT lleva esta versión y se compara al
    # autenticar. Ver DECISION_LOG ADR-13.
    token_version: int = 1
    created_at: datetime


class RefreshToken(BaseModel):
    """Refresh token persistido (se guarda solo su hash, nunca el valor en claro)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    token_hash: str
    expires_at: datetime
    revoked: bool
    created_at: datetime


class TaskList(BaseModel):
    """Lista de tareas. Pertenece al usuario que la creó (`owner_id`)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    title: str
    description: str | None = None
    created_at: datetime


class ListMember(BaseModel):
    """Pertenencia de un usuario a una lista, con su rol (control de acceso)."""

    model_config = ConfigDict(from_attributes=True)

    list_id: int
    user_id: int
    role: ListRole


class OutboxMessage(BaseModel):
    """Mensaje de notificación pendiente de entrega (patrón outbox)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    idempotency_key: str
    email: str
    task_title: str
    status: OutboxStatus
    attempts: int
    last_error: str | None = None
    created_at: datetime


class Task(BaseModel):
    """Tarea perteneciente a una lista."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    list_id: int
    title: str
    description: str | None = None
    status: TaskStatus
    priority: TaskPriority
    assignee_id: int | None = None
    # Contador de versión para concurrencia optimista: se incrementa en cada escritura y se
    # expone como ETag; las mutaciones exigen `If-Match` con esta versión (ver DECISION_LOG
    # ADR-26). Lo gestiona SQLAlchemy vía `version_id_col`.
    version: int = 1
    created_at: datetime
