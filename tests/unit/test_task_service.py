"""Tests unitarios de TaskService (autorización por rol, CRUD, filtros, keyset, asignación)."""

import pytest

from app.application.completion import completion_percentage
from app.application.task_service import TaskService
from app.domain.enums import ListRole, TaskPriority, TaskStatus
from app.domain.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailedError,
)
from tests.unit.fakes import (
    FakeListMemberRepository,
    FakeTaskRepository,
    FakeUserRepository,
)

LIST_ID = 10
EDITOR = 1
VIEWER = 2
OUTSIDER = 3


@pytest.fixture
def members() -> FakeListMemberRepository:
    return FakeListMemberRepository()


@pytest.fixture
def tasks() -> FakeTaskRepository:
    return FakeTaskRepository()


@pytest.fixture
def users() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def service(
    tasks: FakeTaskRepository,
    members: FakeListMemberRepository,
    users: FakeUserRepository,
) -> TaskService:
    return TaskService(task_repository=tasks, member_repository=members, user_repository=users)


@pytest.fixture(autouse=True)
async def _grant_roles(members: FakeListMemberRepository) -> None:
    await members.add(list_id=LIST_ID, user_id=EDITOR, role=ListRole.EDITOR)
    await members.add(list_id=LIST_ID, user_id=VIEWER, role=ListRole.VIEWER)


async def test_create_requires_membership(service: TaskService) -> None:
    with pytest.raises(NotFoundError):  # no es miembro → lista inexistente
        await service.create(user_id=OUTSIDER, list_id=LIST_ID, title="x")


async def test_create_requires_editor_role(service: TaskService) -> None:
    with pytest.raises(ForbiddenError):  # viewer no puede crear
        await service.create(user_id=VIEWER, list_id=LIST_ID, title="x")


async def test_create_and_get(service: TaskService) -> None:
    task, assignee = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")
    assert assignee is None
    fetched = await service.get(LIST_ID, task.id, VIEWER)  # viewer puede leer
    assert fetched.id == task.id


async def test_create_with_assignee_returns_user_to_notify(
    service: TaskService, users: FakeUserRepository
) -> None:
    user = await users.create(email="u@e.com", hashed_password="x")
    _, assignee = await service.create(
        user_id=EDITOR, list_id=LIST_ID, title="t", assignee_id=user.id
    )
    assert assignee is not None and assignee.email == "u@e.com"


async def test_create_with_unknown_assignee_raises(service: TaskService) -> None:
    with pytest.raises(NotFoundError):
        await service.create(user_id=EDITOR, list_id=LIST_ID, title="t", assignee_id=42)


async def test_get_task_from_wrong_list_raises(
    service: TaskService, members: FakeListMemberRepository
) -> None:
    await members.add(list_id=99, user_id=EDITOR, role=ListRole.EDITOR)
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")
    with pytest.raises(NotFoundError):
        await service.get(99, task.id, EDITOR)


async def test_list_tasks_filters_and_keyset(service: TaskService) -> None:
    await service.create(
        user_id=EDITOR,
        list_id=LIST_ID,
        title="a",
        status=TaskStatus.DONE,
        priority=TaskPriority.HIGH,
    )
    await service.create(
        user_id=EDITOR,
        list_id=LIST_ID,
        title="b",
        status=TaskStatus.PENDING,
        priority=TaskPriority.LOW,
    )
    done = await service.list_tasks(
        LIST_ID, VIEWER, status=TaskStatus.DONE, limit=50, after_id=None
    )
    assert len(done) == 1 and done[0].title == "a"
    page1 = await service.list_tasks(LIST_ID, VIEWER, limit=1, after_id=None)
    assert [t.title for t in page1] == ["a"]
    page2 = await service.list_tasks(LIST_ID, VIEWER, limit=1, after_id=page1[0].id)
    assert [t.title for t in page2] == ["b"]


def test_completion_percentage_formula() -> None:
    # Función pura (sin I/O ni autorización): lista vacía → 0.0 (sin división por cero) y
    # redondeo a 2 decimales. El cálculo autorizado se cubre vía `list_tasks_with_completion`.
    assert completion_percentage(0, 0) == 0.0
    assert completion_percentage(3, 1) == pytest.approx(33.33)
    assert completion_percentage(4, 4) == 100.0


async def test_list_tasks_with_completion(service: TaskService) -> None:
    await service.create(user_id=EDITOR, list_id=LIST_ID, title="a", status=TaskStatus.DONE)
    await service.create(user_id=EDITOR, list_id=LIST_ID, title="b", status=TaskStatus.PENDING)
    tasks_page, completion = await service.list_tasks_with_completion(
        LIST_ID, VIEWER, status=TaskStatus.DONE, priority=None, limit=50, after_id=None
    )
    # El filtro afecta a la página; el % se calcula sobre TODAS las tareas de la lista.
    assert [t.title for t in tasks_page] == ["a"]
    assert completion == 50.0
    with pytest.raises(NotFoundError):  # no-miembro: no se salta la autorización (404)
        await service.list_tasks_with_completion(
            LIST_ID, OUTSIDER, priority=None, limit=50, after_id=None
        )


async def test_change_status(service: TaskService) -> None:
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")
    updated = await service.change_status(
        LIST_ID, task.id, EDITOR, TaskStatus.IN_PROGRESS, expected_version=task.version
    )
    assert updated.status == TaskStatus.IN_PROGRESS
    assert updated.version == task.version + 1  # toda escritura sube la versión


async def test_update_task_and_viewer_cannot(service: TaskService) -> None:
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")
    with pytest.raises(ForbiddenError):
        await service.update(
            LIST_ID,
            task.id,
            VIEWER,
            title="x",
            description=None,
            status=TaskStatus.DONE,
            priority=TaskPriority.HIGH,
            assignee_id=None,
            expected_version=None,
        )
    updated, _ = await service.update(
        LIST_ID,
        task.id,
        EDITOR,
        title="nuevo",
        description="d",
        status=TaskStatus.DONE,
        priority=TaskPriority.HIGH,
        assignee_id=None,
        expected_version=None,
    )
    assert updated.title == "nuevo" and updated.status == TaskStatus.DONE


async def test_delete_task(service: TaskService) -> None:
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")
    await service.delete(LIST_ID, task.id, EDITOR, expected_version=task.version)
    with pytest.raises(NotFoundError):
        await service.get(LIST_ID, task.id, EDITOR)


async def test_assign_task(service: TaskService, users: FakeUserRepository) -> None:
    user = await users.create(email="u@e.com", hashed_password="x")
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")
    updated, assigned = await service.assign(
        LIST_ID, task.id, EDITOR, user.id, expected_version=task.version
    )
    assert updated.assignee_id == user.id
    assert assigned.email == "u@e.com"


async def test_assign_unknown_user_raises(service: TaskService) -> None:
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")
    with pytest.raises(NotFoundError):
        await service.assign(LIST_ID, task.id, EDITOR, 999, expected_version=None)


async def _make_non_member_user(users: FakeUserRepository) -> int:
    """Crea un usuario existente cuyo id (= OUTSIDER) NO es miembro de la lista de prueba."""
    user_id = 0
    while user_id != OUTSIDER:  # ids secuenciales 1,2,3...; OUTSIDER=3 no está en _grant_roles
        user_id = (await users.create(email=f"nm{user_id}@e.com", hashed_password="x")).id
    return user_id


async def test_assign_to_existing_non_member_raises_conflict(
    service: TaskService, users: FakeUserRepository
) -> None:
    # Asignar a un usuario que existe pero NO es colaborador de la lista → 409 (ADR-20):
    # mantiene la pertenencia bajo control del owner y evita una invitación a quien no ve la
    # lista. (Un id inexistente seguiría siendo 404; lo cubre test_assign_unknown_user_raises.)
    outsider = await _make_non_member_user(users)
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")
    with pytest.raises(ConflictError):
        await service.assign(LIST_ID, task.id, EDITOR, outsider, expected_version=None)


async def test_create_with_non_member_assignee_raises_conflict(
    service: TaskService, users: FakeUserRepository
) -> None:
    outsider = await _make_non_member_user(users)
    with pytest.raises(ConflictError):
        await service.create(user_id=EDITOR, list_id=LIST_ID, title="t", assignee_id=outsider)


async def test_assign_to_viewer_member_is_allowed(
    service: TaskService, users: FakeUserRepository
) -> None:
    # El asignado solo necesita ser miembro; un `viewer` basta (acceso de lectura, estilo
    # GitHub/Jira). El usuario id=2 es VIEWER en _grant_roles.
    await users.create(email="a@e.com", hashed_password="x")  # id=1 (EDITOR)
    viewer_user = await users.create(email="b@e.com", hashed_password="x")  # id=2 (VIEWER)
    assert viewer_user.id == VIEWER
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")
    updated, assigned = await service.assign(
        LIST_ID, task.id, EDITOR, viewer_user.id, expected_version=task.version
    )
    assert updated.assignee_id == VIEWER and assigned.id == VIEWER


async def test_update_with_assignee_notifies(
    service: TaskService, users: FakeUserRepository
) -> None:
    user = await users.create(email="u@e.com", hashed_password="x")
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")
    _, notify = await service.update(
        LIST_ID,
        task.id,
        EDITOR,
        title="t2",
        description=None,
        status=TaskStatus.DONE,
        priority=TaskPriority.HIGH,
        assignee_id=user.id,
        expected_version=None,
    )
    assert notify is not None and notify.id == user.id


async def test_update_same_assignee_does_not_renotify(
    service: TaskService, users: FakeUserRepository
) -> None:
    user = await users.create(email="u@e.com", hashed_password="x")
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t", assignee_id=user.id)
    _, notify = await service.update(
        LIST_ID,
        task.id,
        EDITOR,
        title="t2",
        description=None,
        status=TaskStatus.DONE,
        priority=TaskPriority.HIGH,
        assignee_id=user.id,
        expected_version=None,
    )
    assert notify is None


async def test_change_status_when_row_vanishes_raises(
    service: TaskService, tasks: FakeTaskRepository
) -> None:
    """Si la tarea se borra entre la verificación y la escritura, se traduce a 404."""
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")

    async def _vanished(*args: object, **kwargs: object) -> None:
        return None

    tasks.update = _vanished  # simula un borrado concurrente

    with pytest.raises(NotFoundError):
        await service.change_status(
            LIST_ID, task.id, EDITOR, TaskStatus.DONE, expected_version=task.version
        )


async def test_delete_when_row_vanishes_raises(
    service: TaskService, tasks: FakeTaskRepository
) -> None:
    """Si la fila desaparece entre la verificación de rol y el borrado, se traduce a 404."""
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")

    async def _gone(*args: object, **kwargs: object) -> bool:
        return False  # simula que otro proceso ya borró la tarea

    tasks.delete = _gone

    with pytest.raises(NotFoundError):
        await service.delete(LIST_ID, task.id, EDITOR, expected_version=task.version)


async def test_update_with_stale_version_raises_precondition_failed(service: TaskService) -> None:
    """If-Match obsoleto (otra escritura subió la versión) → 412, sin pisar el cambio ajeno."""
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")
    # Un primer editor cambia el estado: la versión pasa de 1 a 2.
    await service.change_status(LIST_ID, task.id, EDITOR, TaskStatus.DONE, expected_version=1)
    # Un segundo editor escribe con la versión que leyó antes (1) → conflicto optimista.
    with pytest.raises(PreconditionFailedError):
        await service.update(
            LIST_ID,
            task.id,
            EDITOR,
            title="otro",
            description=None,
            status=TaskStatus.IN_PROGRESS,
            priority=TaskPriority.LOW,
            assignee_id=None,
            expected_version=1,
        )


async def test_wildcard_version_skips_optimistic_check(service: TaskService) -> None:
    """`expected_version=None` (comodín If-Match: *) procede con cualquier versión vigente."""
    task, _ = await service.create(user_id=EDITOR, list_id=LIST_ID, title="t")
    await service.change_status(LIST_ID, task.id, EDITOR, TaskStatus.DONE, expected_version=1)
    updated = await service.change_status(
        LIST_ID, task.id, EDITOR, TaskStatus.IN_PROGRESS, expected_version=None
    )
    assert updated.status == TaskStatus.IN_PROGRESS
