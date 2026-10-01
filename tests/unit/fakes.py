"""Dobles de prueba en memoria que implementan los puertos del dominio."""

from datetime import UTC, datetime

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
from app.domain.security import AccessTokenClaims, IPasswordHasher, ITokenProvider


def _now() -> datetime:
    return datetime.now(UTC)


class FakeListMemberRepository(IListMemberRepository):
    def __init__(self) -> None:
        self.items: dict[tuple[int, int], ListMember] = {}

    async def add(self, *, list_id: int, user_id: int, role: ListRole) -> ListMember:
        member = ListMember(list_id=list_id, user_id=user_id, role=role)
        self.items[(list_id, user_id)] = member
        return member

    async def get(self, list_id: int, user_id: int) -> ListMember | None:
        return self.items.get((list_id, user_id))

    async def list_for_list(
        self, list_id: int, *, limit: int, after_user_id: int | None
    ) -> list[ListMember]:
        members = sorted(
            (m for (lid, _), m in self.items.items() if lid == list_id),
            key=lambda m: m.user_id,
        )
        if after_user_id is not None:
            members = [m for m in members if m.user_id > after_user_id]
        return members[:limit]

    async def set_role(self, list_id: int, user_id: int, role: ListRole) -> ListMember | None:
        current = self.items.get((list_id, user_id))
        if current is None:
            return None
        updated = current.model_copy(update={"role": role})
        self.items[(list_id, user_id)] = updated
        return updated

    async def remove(self, list_id: int, user_id: int) -> bool:
        return self.items.pop((list_id, user_id), None) is not None


class FakeTaskListRepository(ITaskListRepository):
    def __init__(self, members: FakeListMemberRepository) -> None:
        self._items: dict[int, TaskList] = {}
        self._seq = 0
        self._members = members

    async def create(self, *, owner_id: int, title: str, description: str | None) -> TaskList:
        self._seq += 1
        item = TaskList(
            id=self._seq,
            owner_id=owner_id,
            title=title,
            description=description,
            created_at=_now(),
        )
        self._items[item.id] = item
        return item

    async def get(self, list_id: int) -> TaskList | None:
        return self._items.get(list_id)

    async def list_for_member(
        self, user_id: int, *, limit: int, after_id: int | None
    ) -> list[TaskList]:
        member_ids = {lid for (lid, uid) in self._members.items if uid == user_id}
        items = sorted(
            (it for it in self._items.values() if it.id in member_ids), key=lambda it: it.id
        )
        if after_id is not None:
            items = [it for it in items if it.id > after_id]
        return items[:limit]

    async def update(self, list_id: int, *, title: str, description: str | None) -> TaskList | None:
        current = self._items.get(list_id)
        if current is None:
            return None
        updated = current.model_copy(update={"title": title, "description": description})
        self._items[list_id] = updated
        return updated

    async def delete(self, list_id: int) -> bool:
        return self._items.pop(list_id, None) is not None


class FakeTaskRepository(ITaskRepository):
    def __init__(self) -> None:
        self._items: dict[int, Task] = {}
        self._seq = 0

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
        self._seq += 1
        item = Task(
            id=self._seq,
            list_id=list_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
            assignee_id=assignee_id,
            version=1,
            created_at=_now(),
        )
        self._items[item.id] = item
        return item

    async def get(self, task_id: int) -> Task | None:
        return self._items.get(task_id)

    async def list_by_list(
        self,
        list_id: int,
        *,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        limit: int,
        after_id: int | None,
    ) -> list[Task]:
        result = [t for t in self._items.values() if t.list_id == list_id]
        if status is not None:
            result = [t for t in result if t.status == status]
        if priority is not None:
            result = [t for t in result if t.priority == priority]
        result.sort(key=lambda t: t.id)
        if after_id is not None:
            result = [t for t in result if t.id > after_id]
        return result[:limit]

    async def completion_stats(self, list_id: int) -> tuple[int, int]:
        items = [t for t in self._items.values() if t.list_id == list_id]
        done = sum(1 for t in items if t.status == TaskStatus.DONE)
        return len(items), done

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
        current = self._items.get(task_id)
        if current is None:
            return None
        if current.version != expected_version:  # mismo contrato que el repo real (ADR-31)
            raise PreconditionFailedError(f"La tarea {task_id} fue modificada")
        # Espejo de `version_id_col`: toda escritura por ORM incrementa la versión.
        updated = current.model_copy(
            update={
                "title": title,
                "description": description,
                "status": status,
                "priority": priority,
                "assignee_id": assignee_id,
                "version": current.version + 1,
            }
        )
        self._items[task_id] = updated
        return updated

    async def delete(self, task_id: int, *, expected_version: int) -> bool:
        current = self._items.get(task_id)
        if current is None:
            return False
        if current.version != expected_version:
            raise PreconditionFailedError(f"La tarea {task_id} fue modificada")
        del self._items[task_id]
        return True

    async def clear_assignee_in_list(self, list_id: int, user_id: int) -> int:
        count = 0
        for task_id, task in list(self._items.items()):
            if task.list_id == list_id and task.assignee_id == user_id:
                # Igual que el UPDATE masivo real: limpia el responsable y sube la versión.
                self._items[task_id] = task.model_copy(
                    update={"assignee_id": None, "version": task.version + 1}
                )
                count += 1
        return count


class FakeUserRepository(IUserRepository):
    def __init__(self) -> None:
        self._items: dict[int, User] = {}
        self._seq = 0

    async def create(self, *, email: str, hashed_password: str) -> User:
        self._seq += 1
        item = User(id=self._seq, email=email, hashed_password=hashed_password, created_at=_now())
        self._items[item.id] = item
        return item

    async def get(self, user_id: int) -> User | None:
        return self._items.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        return next((u for u in self._items.values() if u.email == email), None)

    async def bump_token_version(self, user_id: int) -> int:
        current = self._items.get(user_id)
        if current is None:
            raise NotFoundError(f"Usuario {user_id} no encontrado")
        updated = current.model_copy(update={"token_version": current.token_version + 1})
        self._items[user_id] = updated
        return updated.token_version


class FakeRefreshTokenRepository(IRefreshTokenRepository):
    def __init__(self) -> None:
        self._items: dict[str, RefreshToken] = {}
        self._seq = 0

    async def add(self, *, user_id: int, token_hash: str, expires_at: datetime) -> RefreshToken:
        self._seq += 1
        item = RefreshToken(
            id=self._seq,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            revoked=False,
            created_at=_now(),
        )
        self._items[token_hash] = item
        return item

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        return self._items.get(token_hash)

    async def revoke(self, token_hash: str) -> None:
        current = self._items.get(token_hash)
        if current is not None:
            self._items[token_hash] = current.model_copy(update={"revoked": True})

    async def revoke_all_for_user(self, user_id: int) -> None:
        for key, item in list(self._items.items()):
            if item.user_id == user_id:
                self._items[key] = item.model_copy(update={"revoked": True})

    async def delete_expired(self, *, now: datetime) -> int:
        expired = [h for h, item in self._items.items() if item.expires_at < now]
        for token_hash in expired:
            del self._items[token_hash]
        return len(expired)


class FakeOutboxRepository(IOutboxRepository):
    def __init__(self) -> None:
        self.items: list[OutboxMessage] = []
        self._seq = 0
        self._keys: set[str] = set()

    async def enqueue(self, *, idempotency_key: str, email: str, task_title: str) -> bool:
        if idempotency_key in self._keys:
            return False
        self._keys.add(idempotency_key)
        self._seq += 1
        self.items.append(
            OutboxMessage(
                id=self._seq,
                idempotency_key=idempotency_key,
                email=email,
                task_title=task_title,
                status=OutboxStatus.PENDING,
                attempts=0,
                last_error=None,
                created_at=_now(),
            )
        )
        return True

    async def fetch_pending(self, limit: int) -> list[OutboxMessage]:
        return [m for m in self.items if m.status == OutboxStatus.PENDING][:limit]

    def _replace(self, message_id: int, **updates: object) -> None:
        self.items = [m.model_copy(update=updates) if m.id == message_id else m for m in self.items]

    async def mark_sent(self, message_id: int) -> None:
        self._replace(message_id, status=OutboxStatus.SENT)

    async def mark_retry(self, message_id: int, error: str) -> None:
        current = next(m for m in self.items if m.id == message_id)
        self._replace(message_id, attempts=current.attempts + 1, last_error=error)

    async def mark_failed(self, message_id: int, error: str) -> None:
        current = next(m for m in self.items if m.id == message_id)
        self._replace(
            message_id,
            attempts=current.attempts + 1,
            last_error=error,
            status=OutboxStatus.FAILED,
        )


class FakePasswordHasher(IPasswordHasher):
    def hash(self, plain: str) -> str:
        return f"hashed::{plain}"

    def verify(self, plain: str, hashed: str) -> bool:
        return hashed == f"hashed::{plain}"


class FakeTokenProvider(ITokenProvider):
    def create_access_token(self, *, subject: str, token_version: int) -> str:
        return f"token::{subject}::{token_version}"

    def decode(self, token: str) -> AccessTokenClaims:
        _, subject, version = token.split("::")
        return AccessTokenClaims(subject=subject, token_version=int(version))
