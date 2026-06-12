"""Auth (token_version + refresh_tokens), RBAC (list_members) y outbox de notificaciones.

Revision ID: 0002_auth_rbac_outbox
Revises: 0001_initial
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_auth_rbac_outbox"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_list_role = sa.Enum("viewer", "editor", "owner", name="list_role")
_outbox_status = sa.Enum("pending", "sent", "failed", name="outbox_status")


def upgrade() -> None:
    # --- Auth: versión de token para revocación global ---
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
    )

    # --- Auth: refresh tokens (se guarda solo el hash) ---
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)

    # --- RBAC: pertenencia de usuarios a listas con rol ---
    op.create_table(
        "list_members",
        sa.Column(
            "list_id",
            sa.Integer(),
            sa.ForeignKey("task_lists.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", _list_role, nullable=False),
    )

    # --- Outbox de notificaciones ---
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("task_title", sa.String(length=255), nullable=False),
        sa.Column("status", _outbox_status, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_notification_outbox_idempotency_key",
        "notification_outbox",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index("ix_notification_outbox_status", "notification_outbox", ["status"])


def downgrade() -> None:
    op.drop_index("ix_notification_outbox_status", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_idempotency_key", table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_table("list_members")
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_column("users", "token_version")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _outbox_status.drop(bind, checkfirst=True)
        _list_role.drop(bind, checkfirst=True)
