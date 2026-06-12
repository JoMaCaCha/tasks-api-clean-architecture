"""Base declarativa de SQLAlchemy 2.0."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Clase base para todos los modelos ORM."""
