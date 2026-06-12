"""Entorno de migraciones Alembic (SQLAlchemy 2.0 async).

La URL se lee de ``DATABASE_URL`` (no de ``alembic.ini`` ni del ``Settings`` de la app)
para desacoplar las migraciones del resto de la configuración —en particular del
``JWT_SECRET_KEY`` obligatorio—. Importar ``app.infrastructure.db`` registra todos los
modelos en ``Base.metadata``, requisito para que ``autogenerate`` detecte las tablas.
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

# Importa el paquete de persistencia: registra los modelos ORM en Base.metadata.
from app.infrastructure.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL efectiva: variable de entorno con el mismo default de desarrollo que la app.
_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://crehana:crehana@db:5432/crehana_tasks"
)
config.set_main_option("sqlalchemy.url", _DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Genera el SQL sin conectarse (modo ``--sql``)."""
    context.configure(
        url=_DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Crea un motor async (NullPool: una conexión efímera por corrida) y migra."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
