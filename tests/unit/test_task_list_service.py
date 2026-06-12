"""Tests unitarios de TaskListService (roles de colaborador, keyset y membresías)."""

import pytest

from app.application.task_list_service import TaskListService
from app.domain.enums import ListRole, TaskPriority, TaskStatus
from app.domain.exceptions import ConflictError, ForbiddenError, NotFoundError
from tests.unit.fakes import (
    FakeListMemberRepository,
    FakeTaskListRepository,
    FakeTaskRepository,
    FakeUserRepository,
)

OWNER = 1
COLLAB = 2
OUTSIDER = 3


@pytest.fixture
def members() -> FakeListMemberRepository:
    return FakeListMemberRepository()


@pytest.fixture
def users() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def tasks() -> FakeTaskRepository:
    return FakeTaskRepository()


@pytest.fixture
def service(
    members: FakeListMemberRepository,
    users: FakeUserRepository,
    tasks: FakeTaskRepository,
) -> TaskListService:
    lists = FakeTaskListRepository(members)
    return TaskListService(
        repository=lists,
        member_repository=members,
        user_repository=users,
        task_repository=tasks,
    )


async def _seed_users(users: FakeUserRepository, count: int) -> None:
    for i in range(count):
        await users.create(email=f"u{i}@e.com", hashed_password="x")


async def test_create_makes_creator_owner(
    service: TaskListService, members: FakeListMemberRepository
) -> None:
    created = await service.create(owner_id=OWNER, title="L")
    membership = await members.get(created.id, OWNER)
    assert membership is not None and membership.role == ListRole.OWNER


async def test_get_requires_membership(service: TaskListService) -> None:
    created = await service.create(owner_id=OWNER, title="L")
    assert (await service.get(created.id, OWNER)).id == created.id
    with pytest.raises(NotFoundError):  # ajeno: 404 (no se filtra existencia)
        await service.get(created.id, OUTSIDER)


async def test_get_with_completion(service: TaskListService, tasks: FakeTaskRepository) -> None:
    created = await service.create(owner_id=OWNER, title="L")
    # Sin tareas → 0.0; una sola comprobación de rol resuelve lista + completitud.
    task_list, completion = await service.get_with_completion(created.id, OWNER)
    assert task_list.id == created.id and completion == 0.0
    await tasks.create(
        list_id=created.id,
        title="a",
        description=None,
        status=TaskStatus.DONE,
        priority=TaskPriority.MEDIUM,
        assignee_id=None,
    )
    await tasks.create(
        list_id=created.id,
        title="b",
        description=None,
        status=TaskStatus.PENDING,
        priority=TaskPriority.MEDIUM,
        assignee_id=None,
    )
    _, completion = await service.get_with_completion(created.id, OWNER)
    assert completion == 50.0
    # Ajeno: 404, no se filtra existencia.
    with pytest.raises(NotFoundError):
        await service.get_with_completion(created.id, OUTSIDER)


async def test_list_all_scoped_and_keyset(service: TaskListService) -> None:
    for i in range(3):
        await service.create(owner_id=OWNER, title=f"L{i}")
    page1 = await service.list_all(OWNER, limit=2, after_id=None)
    assert [item.id for item in page1] == [1, 2]
    page2 = await service.list_all(OWNER, limit=2, after_id=2)
    assert [item.id for item in page2] == [3]
    assert await service.list_all(OUTSIDER, limit=50, after_id=None) == []


async def test_update_requires_editor(service: TaskListService, users: FakeUserRepository) -> None:
    await _seed_users(users, 3)
    created = await service.create(owner_id=OWNER, title="L")
    await service.add_member(created.id, OWNER, user_id=COLLAB, role=ListRole.VIEWER)
    with pytest.raises(ForbiddenError):  # viewer no puede editar
        await service.update(created.id, COLLAB, title="hack")
    await service.set_member_role(created.id, OWNER, user_id=COLLAB, role=ListRole.EDITOR)
    updated = await service.update(created.id, COLLAB, title="ok")
    assert updated.title == "ok"


async def test_delete_requires_owner(service: TaskListService, users: FakeUserRepository) -> None:
    await _seed_users(users, 3)
    created = await service.create(owner_id=OWNER, title="L")
    await service.add_member(created.id, OWNER, user_id=COLLAB, role=ListRole.EDITOR)
    with pytest.raises(ForbiddenError):  # editor no puede borrar
        await service.delete(created.id, COLLAB)
    await service.delete(created.id, OWNER)
    with pytest.raises(NotFoundError):
        await service.get(created.id, OWNER)


async def test_add_member_flow(service: TaskListService, users: FakeUserRepository) -> None:
    await _seed_users(users, 3)
    created = await service.create(owner_id=OWNER, title="L")
    await service.add_member(created.id, OWNER, user_id=COLLAB, role=ListRole.VIEWER)
    members = await service.list_members(created.id, OWNER, limit=50, after_user_id=None)
    member_ids = {m.user_id for m in members}
    assert member_ids == {OWNER, COLLAB}
    # El colaborador ya puede ver la lista.
    assert (await service.get(created.id, COLLAB)).id == created.id


async def test_add_member_requires_owner(
    service: TaskListService, users: FakeUserRepository
) -> None:
    await _seed_users(users, 3)
    created = await service.create(owner_id=OWNER, title="L")
    await service.add_member(created.id, OWNER, user_id=COLLAB, role=ListRole.EDITOR)
    with pytest.raises(ForbiddenError):  # un editor no gestiona miembros
        await service.add_member(created.id, COLLAB, user_id=OUTSIDER, role=ListRole.VIEWER)


async def test_add_unknown_user_raises(service: TaskListService) -> None:
    created = await service.create(owner_id=OWNER, title="L")
    with pytest.raises(NotFoundError):
        await service.add_member(created.id, OWNER, user_id=999, role=ListRole.VIEWER)


async def test_add_duplicate_member_raises(
    service: TaskListService, users: FakeUserRepository
) -> None:
    await _seed_users(users, 3)
    created = await service.create(owner_id=OWNER, title="L")
    await service.add_member(created.id, OWNER, user_id=COLLAB, role=ListRole.VIEWER)
    with pytest.raises(ConflictError):
        await service.add_member(created.id, OWNER, user_id=COLLAB, role=ListRole.EDITOR)


async def test_cannot_modify_original_owner(
    service: TaskListService, users: FakeUserRepository
) -> None:
    await _seed_users(users, 3)
    created = await service.create(owner_id=OWNER, title="L")
    with pytest.raises(ForbiddenError):
        await service.set_member_role(created.id, OWNER, user_id=OWNER, role=ListRole.VIEWER)
    with pytest.raises(ForbiddenError):
        await service.remove_member(created.id, OWNER, user_id=OWNER)


async def test_promoted_owner_can_be_demoted_but_list_keeps_original_owner(
    service: TaskListService, users: FakeUserRepository
) -> None:
    """Invariante: la lista nunca queda huérfana de owner.

    Un colaborador promovido a `owner` SÍ puede degradarse o expulsarse (no es el creador),
    pero el `owner_id` original queda anclado por `_guard_original_owner`, de modo que
    siempre permanece al menos un owner. Por eso no hace falta un chequeo extra de
    "¿queda algún owner?": el ancla del creador ya lo garantiza.
    """
    await _seed_users(users, 3)
    created = await service.create(owner_id=OWNER, title="L")
    # Se promueve al colaborador a un segundo owner...
    await service.add_member(created.id, OWNER, user_id=COLLAB, role=ListRole.OWNER)
    # ...y se le degrada de vuelta: permitido, porque no es el owner original.
    await service.set_member_role(created.id, OWNER, user_id=COLLAB, role=ListRole.VIEWER)
    # El owner original sigue siendo owner: la lista conserva un owner en todo momento.
    original = await service.list_members(created.id, OWNER, limit=50, after_user_id=None)
    assert next(m.role for m in original if m.user_id == OWNER) == ListRole.OWNER


async def test_remove_member(service: TaskListService, users: FakeUserRepository) -> None:
    await _seed_users(users, 3)
    created = await service.create(owner_id=OWNER, title="L")
    await service.add_member(created.id, OWNER, user_id=COLLAB, role=ListRole.VIEWER)
    await service.remove_member(created.id, OWNER, user_id=COLLAB)
    with pytest.raises(NotFoundError):
        await service.get(created.id, COLLAB)


async def test_remove_member_unassigns_their_tasks(
    service: TaskListService, users: FakeUserRepository, tasks: FakeTaskRepository
) -> None:
    """Invariante de ADR-20: al expulsar a un colaborador, sus tareas se desasignan.

    Un no-miembro no puede seguir figurando como responsable; sus tareas en ESA lista
    quedan con `assignee_id = None`. Las tareas de OTRAS listas (donde sigue siendo
    miembro) no se tocan.
    """
    await _seed_users(users, 3)
    created = await service.create(owner_id=OWNER, title="L")
    await service.add_member(created.id, OWNER, user_id=COLLAB, role=ListRole.EDITOR)
    assigned = await tasks.create(
        list_id=created.id,
        title="suya",
        description=None,
        status=TaskStatus.PENDING,
        priority=TaskPriority.MEDIUM,
        assignee_id=COLLAB,
    )
    # Tarea en otra lista, asignada al mismo usuario: no debe verse afectada.
    elsewhere = await tasks.create(
        list_id=999,
        title="ajena",
        description=None,
        status=TaskStatus.PENDING,
        priority=TaskPriority.MEDIUM,
        assignee_id=COLLAB,
    )

    await service.remove_member(created.id, OWNER, user_id=COLLAB)

    refreshed = await tasks.get(assigned.id)
    assert refreshed is not None and refreshed.assignee_id is None
    untouched = await tasks.get(elsewhere.id)
    assert untouched is not None and untouched.assignee_id == COLLAB
