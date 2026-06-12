"""Modelos ORM (SQLAlchemy 2.0, estilo `Mapped`/`mapped_column`).

Las relaciones se resuelven con consultas explícitas en los repositorios (no se usan
`relationship` perezosas) para mantener un comportamiento predecible en modo async.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import ListRole, OutboxStatus, TaskPriority, TaskStatus
from app.infrastructure.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Guarda el *valor* del enum ("pending") en lugar del nombre ("PENDING").
_STATUS_ENUM = Enum(TaskStatus, name="task_status", values_callable=lambda e: [m.value for m in e])
_PRIORITY_ENUM = Enum(
    TaskPriority, name="task_priority", values_callable=lambda e: [m.value for m in e]
)
_ROLE_ENUM = Enum(ListRole, name="list_role", values_callable=lambda e: [m.value for m in e])
_OUTBOX_STATUS_ENUM = Enum(
    OutboxStatus, name="outbox_status", values_callable=lambda e: [m.value for m in e]
)


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    token_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RefreshTokenModel(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # SHA-256 (hex) del token en claro: nunca se persiste el valor original.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TaskListModel(Base):
    __tablename__ = "task_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ListMemberModel(Base):
    __tablename__ = "list_members"

    list_id: Mapped[int] = mapped_column(
        ForeignKey("task_lists.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[ListRole] = mapped_column(_ROLE_ENUM)


class OutboxMessageModel(Base):
    __tablename__ = "notification_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320))
    task_title: Mapped[str] = mapped_column(String(255))
    status: Mapped[OutboxStatus] = mapped_column(
        _OUTBOX_STATUS_ENUM, default=OutboxStatus.PENDING, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TaskModel(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(
        ForeignKey("task_lists.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(_STATUS_ENUM, default=TaskStatus.PENDING)
    priority: Mapped[TaskPriority] = mapped_column(_PRIORITY_ENUM, default=TaskPriority.MEDIUM)
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Concurrencia optimista: SQLAlchemy gestiona esta columna como `version_id_col` (ver
    # `__mapper_args__`). En cada UPDATE/DELETE por ORM añade `WHERE version = :actual` y la
    # incrementa; si ninguna fila coincide (otra transacción ya la cambió) lanza
    # `StaleDataError`, que el repositorio traduce a 412. La versión se expone como ETag y las
    # mutaciones exigen `If-Match` (ver DECISION_LOG ADR-26).
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __mapper_args__ = {"version_id_col": version}
