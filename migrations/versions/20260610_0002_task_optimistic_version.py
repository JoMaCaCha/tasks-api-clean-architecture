"""Concurrencia optimista en tareas: columna `version` (version_id_col).

Revision ID: 0003_task_version
Revises: 0002_auth_rbac_outbox
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_task_version"
down_revision: str | None = "0002_auth_rbac_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `server_default="1"` siembra las filas existentes (y cualquier INSERT ajeno al ORM) con
    # la versión inicial; los INSERT por ORM fijan el valor explícitamente vía version_id_col.
    op.add_column(
        "tasks",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("tasks", "version")
