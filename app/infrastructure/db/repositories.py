"""Implementaciones SQLAlchemy async de los repositorios del dominio.

Cada método traduce entre modelos ORM y entidades de dominio (vía `model_validate`),
de modo que la capa `application` nunca ve objetos de SQLAlchemy.

**Transacciones (unidad de trabajo).** Los métodos de escritura hacen ``flush`` (no
``commit``): el límite transaccional lo fija quien abre la sesión —la request
(`get_session`), el worker del outbox o un helper de tests—, de modo que varias
escrituras (incluido el encolado en el outbox) sean atómicas. Ver DECISION_LOG ADR-15.
"""

from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Select, case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.domain.entities import (
    ListMember,
    OutboxMessage,
    RefreshToken,
    Task,
    TaskList,
    User,
)
from app.domain.enums import ListRole, OutboxStatus, TaskPriority, TaskStatus
from app.domain.exceptions import NotFoundError, PreconditionFailedError
from app.domain.repositories import (
    IListMemberRepository,
    IOutboxRepository,
    IRefreshTokenRepository,
    ITaskListRepository,
    ITaskRepository,
    IUserRepository,
)
from app.infrastructure.db.models import (
    ListMemberModel,
    OutboxMessageModel,
    RefreshTokenModel,
    TaskListModel,
    TaskModel,
    UserModel,
)


def _stale_task(task_id: int) -> PreconditionFailedError:
    """Error 412 uniforme cuando una escritura por ORM choca con `version_id_col`."""
    return PreconditionFailedError(
        f"La tarea {task_id} fue modificada por otra operación; "
        "vuelve a leerla (nuevo ETag) y reintenta."
    )


class SqlAlchemyTaskListRepository(ITaskListRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, owner_id: int, title: str, description: str | None) -> TaskList:
        model = TaskListModel(owner_id=owner_id, title=title, description=description)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return TaskList.model_validate(model)

    async def get(self, list_id: int) -> TaskList | None:
        model = await self._session.get(TaskListModel, list_id)
        return TaskList.model_validate(model) if model is not None else None

    async def list_for_member(
        self, user_id: int, *, limit: int, after_id: int | None
    ) -> list[TaskList]:
        stmt = (
            select(TaskListModel)
            .join(ListMemberModel, ListMemberModel.list_id == TaskListModel.id)
            .where(ListMemberModel.user_id == user_id)
        )
        if after_id is not None:
            stmt = stmt.where(TaskListModel.id > after_id)
        stmt = stmt.order_by(TaskListModel.id).limit(limit)
        result = await self._session.execute(stmt)
        return [TaskList.model_validate(m) for m in result.scalars().all()]

    async def update(self, list_id: int, *, title: str, description: str | None) -> TaskList | None:
        model = await self._session.get(TaskListModel, list_id)
        if model is None:
            return None
        model.title = title
        model.description = description
        await self._session.flush()
        await self._session.refresh(model)
        return TaskList.model_validate(model)

    async def delete(self, list_id: int) -> bool:
        model = await self._session.get(TaskListModel, list_id)
        if model is None:
            return False
        # Borrado explícito de hijos (portable entre PostgreSQL y SQLite).
        await self._session.execute(delete(TaskModel).where(TaskModel.list_id == list_id))
        await self._session.execute(
            delete(ListMemberModel).where(ListMemberModel.list_id == list_id)
        )
        await self._session.execute(delete(TaskListModel).where(TaskListModel.id == list_id))
        await self._session.flush()
        return True


class SqlAlchemyListMemberRepository(IListMemberRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, *, list_id: int, user_id: int, role: ListRole) -> ListMember:
        model = ListMemberModel(list_id=list_id, user_id=user_id, role=role)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return ListMember.model_validate(model)

    async def get(self, list_id: int, user_id: int) -> ListMember | None:
        model = await self._session.get(ListMemberModel, (list_id, user_id))
        return ListMember.model_validate(model) if model is not None else None

    async def list_for_list(
        self, list_id: int, *, limit: int, after_user_id: int | None
    ) -> list[ListMember]:
        stmt = select(ListMemberModel).where(ListMemberModel.list_id == list_id)
        if after_user_id is not None:
            stmt = stmt.where(ListMemberModel.user_id > after_user_id)
        stmt = stmt.order_by(ListMemberModel.user_id).limit(limit)
        result = await self._session.execute(stmt)
        return [ListMember.model_validate(m) for m in result.scalars().all()]

    async def set_role(self, list_id: int, user_id: int, role: ListRole) -> ListMember | None:
        model = await self._session.get(ListMemberModel, (list_id, user_id))
        if model is None:
            return None
        model.role = role
        await self._session.flush()
        await self._session.refresh(model)
        return ListMember.model_validate(model)

    async def remove(self, list_id: int, user_id: int) -> bool:
        model = await self._session.get(ListMemberModel, (list_id, user_id))
        if model is None:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True


class SqlAlchemyTaskRepository(ITaskRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        list_id: int,
        title: str,
        description: str | None,
        status: TaskStatus,
        priority: TaskPriority,
        assignee_id: int | None,
    ) -> Task:
        model = TaskModel(
            list_id=list_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
            assignee_id=assignee_id,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return Task.model_validate(model)

    async def get(self, task_id: int) -> Task | None:
        model = await self._session.get(TaskModel, task_id)
        return Task.model_validate(model) if model is not None else None

    async def list_by_list(
        self,
        list_id: int,
        *,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        limit: int,
        after_id: int | None,
    ) -> list[Task]:
        stmt = select(TaskModel).where(TaskModel.list_id == list_id)
        if status is not None:
            stmt = stmt.where(TaskModel.status == status)
        if priority is not None:
            stmt = stmt.where(TaskModel.priority == priority)
        if after_id is not None:
            stmt = stmt.where(TaskModel.id > after_id)
        stmt = stmt.order_by(TaskModel.id).limit(limit)
        result = await self._session.execute(stmt)
        return [Task.model_validate(m) for m in result.scalars().all()]

    async def completion_stats(self, list_id: int) -> tuple[int, int]:
        # COUNT ignora los NULL, así que el CASE cuenta solo las tareas en estado DONE.
        done_count = func.count(case((TaskModel.status == TaskStatus.DONE, TaskModel.id)))
        stmt = select(func.count(TaskModel.id), done_count).where(TaskModel.list_id == list_id)
        total, done = (await self._session.execute(stmt)).one()
        return int(total), int(done)

    async def update(
        self,
        task_id: int,
        *,
        expected_version: int,
        title: str,
        description: str | None,
        status: TaskStatus,
        priority: TaskPriority,
        assignee_id: int | None,
    ) -> Task | None:
        model = await self._session.get(TaskModel, task_id)
        if model is None:
            return None
        # La instancia que leyó el servicio puede ya no estar en el identity map (SQLAlchemy solo
        # guarda referencias débiles), así que `get` pudo releer la fila. Si otra transacción la
        # cambió entretanto, la versión releída ya no es la validada contra `If-Match`: sin esta
        # comprobación, `version_id_col` filtraría por la versión NUEVA y esta escritura pisaría
        # a la otra (*lost update*). Ver DECISION_LOG ADR-31.
        if model.version != expected_version:
            raise _stale_task(task_id)
        model.title = title
        model.description = description
        model.status = status
        model.priority = priority
        model.assignee_id = assignee_id
        # `version_id_col` añade `WHERE version = :actual` y la incrementa en este flush. Si
        # otra transacción cambió la fila entre la lectura del servicio (chequeo de `If-Match`)
        # y este UPDATE, no coincide ninguna fila → `StaleDataError`, que se traduce a 412
        # (cierra la ventana TOCTOU residual; el caso común ya lo detecta el servicio).
        try:
            await self._session.flush()
        except StaleDataError as exc:
            raise _stale_task(task_id) from exc
        await self._session.refresh(model)
        return Task.model_validate(model)

    async def delete(self, task_id: int, *, expected_version: int) -> bool:
        model = await self._session.get(TaskModel, task_id)
        if model is None:
            return False
        if model.version != expected_version:  # misma ventana que en `update` (ADR-31)
            raise _stale_task(task_id)
        await self._session.delete(model)
        # El DELETE por ORM también lleva `WHERE version = :actual` (version_id_col): si la fila
        # cambió tras la lectura del servicio, `StaleDataError` → 412, igual que en `update`.
        try:
            await self._session.flush()
        except StaleDataError as exc:
            raise _stale_task(task_id) from exc
        return True

    async def clear_assignee_in_list(self, list_id: int, user_id: int) -> int:
        # UPDATE en un solo statement (sin materializar filas): pone a NULL el responsable de
        # las tareas de la lista asignadas a `user_id`. Mantiene la invariante de ADR-20 al
        # quitar a un colaborador. El commit lo hace la unidad de trabajo (la request).
        # Incrementa `version` también aquí para conservar la invariante "toda modificación
        # sube la versión": los UPDATE masivos NO pasan por `version_id_col` (solo el flush de
        # filas individuales), así que se bumpea de forma explícita y un cliente con un ETag
        # previo no podrá sobrescribir una tarea cuyo responsable se acaba de limpiar.
        result = await self._session.execute(
            update(TaskModel)
            .where(TaskModel.list_id == list_id, TaskModel.assignee_id == user_id)
            .values(assignee_id=None, version=TaskModel.version + 1)
        )
        await self._session.flush()
        return cast("CursorResult[Any]", result).rowcount


class SqlAlchemyUserRepository(IUserRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, email: str, hashed_password: str) -> User:
        model = UserModel(email=email, hashed_password=hashed_password)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return User.model_validate(model)

    async def get(self, user_id: int) -> User | None:
        model = await self._session.get(UserModel, user_id)
        return User.model_validate(model) if model is not None else None

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(UserModel).where(UserModel.email == email))
        model = result.scalar_one_or_none()
        return User.model_validate(model) if model is not None else None

    async def bump_token_version(self, user_id: int) -> int:
        model = await self._session.get(UserModel, user_id)
        if model is None:
            raise NotFoundError(f"Usuario {user_id} no encontrado")
        model.token_version += 1
        await self._session.flush()
        await self._session.refresh(model)
        return model.token_version


class SqlAlchemyRefreshTokenRepository(IRefreshTokenRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, *, user_id: int, token_hash: str, expires_at: datetime) -> RefreshToken:
        model = RefreshTokenModel(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return RefreshToken.model_validate(model)

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self._session.execute(
            select(RefreshTokenModel).where(RefreshTokenModel.token_hash == token_hash)
        )
        model = result.scalar_one_or_none()
        return RefreshToken.model_validate(model) if model is not None else None

    async def revoke(self, token_hash: str) -> None:
        await self._session.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.token_hash == token_hash)
            .values(revoked=True)
        )
        await self._session.flush()

    async def revoke_all_for_user(self, user_id: int) -> None:
        await self._session.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.user_id == user_id)
            .values(revoked=True)
        )
        await self._session.flush()

    async def delete_expired(self, *, now: datetime) -> int:
        # DELETE en un solo statement (sin materializar filas). `rowcount` reporta cuántas
        # se borraron en PostgreSQL y SQLite. Solo borra `expires_at < now`: preserva la
        # detección de reúso sobre tokens vigentes (ADR-13/ADR-21). El commit lo hace quien
        # abre la sesión (el worker de mantenimiento), igual que el resto de repos.
        result = await self._session.execute(
            delete(RefreshTokenModel).where(RefreshTokenModel.expires_at < now)
        )
        await self._session.flush()
        # `execute` se tipa como `Result[Any]`; el objeto real de un DELETE es `CursorResult`,
        # que sí expone `rowcount` (nº de filas borradas). Cast explícito para mypy --strict.
        return cast("CursorResult[Any]", result).rowcount


class SqlAlchemyOutboxRepository(IOutboxRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, *, idempotency_key: str, email: str, task_title: str) -> bool:
        # Inserción idempotente **atómica** con `INSERT ... ON CONFLICT DO NOTHING` (un solo
        # statement, no un SELECT-then-INSERT): si dos requests concurrentes encolan la misma
        # clave dentro de la misma unidad de trabajo de negocio, una inserta y la otra se
        # omite **sin** `IntegrityError` que aborte la transacción (lo que antes podía dejar
        # la escritura de negocio sin commit → 500). `RETURNING id` indica si la fila se
        # insertó (clave nueva → True) o se omitió (ya existía → False). Validado en
        # PostgreSQL y SQLite (aiosqlite). El commit lo hace la request. Ver ADR-15.
        # Solo PostgreSQL (producción) y SQLite (tests) exponen `on_conflict_do_nothing`.
        # Se valida el dialecto de forma **explícita** y se falla rápido ante uno no soportado:
        # asumir SQLite en silencio para cualquier otro motor (MySQL, etc.) emitiría SQL
        # inválido para ese backend. La expresión condicional posterior mantiene el tipado de
        # unión de `insert_stmt` (mypy --strict) necesario para `on_conflict_do_nothing`.
        dialect = self._session.bind.dialect.name
        if dialect not in ("postgresql", "sqlite"):
            raise RuntimeError(
                f"El outbox idempotente requiere 'INSERT ... ON CONFLICT' y no soporta el "
                f"dialecto '{dialect}'. Dialectos admitidos: postgresql, sqlite."
            )
        insert_stmt = pg_insert if dialect == "postgresql" else sqlite_insert
        stmt = (
            insert_stmt(OutboxMessageModel)
            .values(idempotency_key=idempotency_key, email=email, task_title=task_title)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(OutboxMessageModel.id)
        )
        inserted = (await self._session.execute(stmt)).first()
        await self._session.flush()
        return inserted is not None

    @staticmethod
    def _pending_stmt(limit: int) -> Select[tuple[OutboxMessageModel]]:
        # FOR UPDATE SKIP LOCKED bloquea las filas tomadas hasta el commit de la tanda, de
        # modo que varias réplicas del worker no entreguen el mismo mensaje (en SQLite es
        # un no-op inofensivo). Aislado como statement para poder verificar de forma
        # determinista que el dialecto PostgreSQL emite la cláusula de locking (ver tests).
        return (
            select(OutboxMessageModel)
            .where(OutboxMessageModel.status == OutboxStatus.PENDING)
            .order_by(OutboxMessageModel.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

    async def fetch_pending(self, limit: int) -> list[OutboxMessage]:
        # Quien procesa la tanda es responsable del commit (mantiene el lock hasta marcar).
        result = await self._session.execute(self._pending_stmt(limit))
        return [OutboxMessage.model_validate(m) for m in result.scalars().all()]

    async def mark_sent(self, message_id: int) -> None:
        await self._session.execute(
            update(OutboxMessageModel)
            .where(OutboxMessageModel.id == message_id)
            .values(status=OutboxStatus.SENT)
        )
        await self._session.flush()

    async def mark_retry(self, message_id: int, error: str) -> None:
        await self._session.execute(
            update(OutboxMessageModel)
            .where(OutboxMessageModel.id == message_id)
            .values(attempts=OutboxMessageModel.attempts + 1, last_error=error)
        )
        await self._session.flush()

    async def mark_failed(self, message_id: int, error: str) -> None:
        await self._session.execute(
            update(OutboxMessageModel)
            .where(OutboxMessageModel.id == message_id)
            .values(
                attempts=OutboxMessageModel.attempts + 1,
                last_error=error,
                status=OutboxStatus.FAILED,
            )
        )
        await self._session.flush()
