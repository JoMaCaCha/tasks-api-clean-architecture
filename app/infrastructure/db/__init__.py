"""Persistencia: modelos SQLAlchemy 2.0 async, motor/sesión y repositorios.

Importar este paquete registra los modelos ORM en ``Base.metadata`` (efecto necesario
para que ``Base.metadata.create_all`` conozca todas las tablas). Los símbolos se declaran
en ``__all__`` como **re-exports intencionales**: así el registro ocurre en un único
lugar y el linter los reconoce como API pública, sin supresiones por línea.
"""

from app.infrastructure.db.base import Base
from app.infrastructure.db.models import (
    ListMemberModel,
    OutboxMessageModel,
    RefreshTokenModel,
    TaskListModel,
    TaskModel,
    UserModel,
)

__all__ = [
    "Base",
    "ListMemberModel",
    "OutboxMessageModel",
    "RefreshTokenModel",
    "TaskListModel",
    "TaskModel",
    "UserModel",
]
