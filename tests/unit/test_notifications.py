"""Tests del notificador: factoría por config y envío SMTP (con doble del transporte)."""

from typing import Any

from app.infrastructure.config import Settings
from app.infrastructure.notifications import (
    LoggingEmailNotifier,
    SmtpEmailNotifier,
    build_notifier,
)

_SECRET = "test-secret-key-with-at-least-32-chars"


def _settings(**overrides: Any) -> Settings:
    return Settings(jwt_secret_key=_SECRET, **overrides)


def test_build_notifier_defaults_to_log() -> None:
    assert isinstance(build_notifier(_settings()), LoggingEmailNotifier)


def test_build_notifier_smtp_backend() -> None:
    notifier = build_notifier(_settings(notifier_backend="smtp", smtp_host="mail.local"))
    assert isinstance(notifier, SmtpEmailNotifier)


async def test_logging_notifier_does_not_raise() -> None:
    await LoggingEmailNotifier().notify_task_assignment(email="a@e.com", task_title="t")


class _FakeSMTP:
    """Doble del transporte SMTP (context manager) que captura el mensaje enviado."""

    sent: list[tuple[str, str]] = []

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host = host
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None

    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, message: Any) -> None:
        _FakeSMTP.sent.append((message["To"], message["Subject"]))


async def test_smtp_notifier_sends_message(monkeypatch: Any) -> None:
    _FakeSMTP.sent.clear()
    monkeypatch.setattr("app.infrastructure.notifications.smtplib.SMTP", _FakeSMTP)
    notifier = SmtpEmailNotifier(
        host="mail.local",
        port=587,
        username="user",
        password="pass",
        sender="no-reply@x.com",
        use_tls=True,
    )
    await notifier.notify_task_assignment(email="dest@e.com", task_title="Tarea X")
    assert _FakeSMTP.sent == [("dest@e.com", "Se te ha asignado una tarea")]
