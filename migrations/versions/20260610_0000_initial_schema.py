"""Esquema inicial: users, task_lists (con owner_id) y tasks.

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Enums persistidos por su *valor* (minúsculas), igual que en los modelos ORM.
_task_status = sa.Enum("pending", "in_progress", "done", name="task_status")
_task_priority = sa.Enum("low", "medium", "high", name="task_priority")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "task_lists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_lists_owner_id", "task_lists", ["owner_id"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "list_id",
            sa.Integer(),
            sa.ForeignKey("task_lists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", _task_status, nullable=False),
        sa.Column("priority", _task_priority, nullable=False),
        sa.Column(
            "assignee_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tasks_list_id", "tasks", ["list_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_list_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_task_lists_owner_id", table_name="task_lists")
    op.drop_table("task_lists")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    # Los tipos ENUM se eliminan explícitamente en PostgreSQL (no en SQLite).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _task_status.drop(bind, checkfirst=True)
        _task_priority.drop(bind, checkfirst=True)
