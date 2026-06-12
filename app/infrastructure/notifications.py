"""Notificadores de email: simulado (log) y real (SMTP), tras el puerto `INotifier`.

El backend se elige por configuración (`NOTIFIER_BACKEND`): `log` simula el envío
(§1.b.iv) y `smtp` envía de verdad. El envío SMTP es bloqueante, así que se ejecuta en
un hilo (`asyncio.to_thread`) para no bloquear el event loop. Ver DECISION_LOG ADR-9.
"""

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.domain.notifications import INotifier
from app.infrastructure.config import Settings

logger = logging.getLogger("app.notifications")


class LoggingEmailNotifier(INotifier):
    """Simula el envío de un email escribiendo en el log (no envía nada real)."""

    async def notify_task_assignment(self, *, email: str, task_title: str) -> None:
        logger.info(
            "[email simulado] Para: %s — Se te ha asignado la tarea: %r",
            email,
            task_title,
        )


class SmtpEmailNotifier(INotifier):
    """Envía la invitación por SMTP (envío real)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        sender: str,
        use_tls: bool,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._use_tls = use_tls

    async def notify_task_assignment(self, *, email: str, task_title: str) -> None:
        await asyncio.to_thread(self._send, email, task_title)

    def _send(self, email: str, task_title: str) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = email
        message["Subject"] = "Se te ha asignado una tarea"
        message.set_content(f"Se te ha asignado la tarea: {task_title}")
        with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._username and self._password:
                smtp.login(self._username, self._password)
            smtp.send_message(message)


def build_notifier(settings: Settings) -> INotifier:
    """Construye el notificador según `NOTIFIER_BACKEND` (`log` por defecto)."""
    if settings.notifier_backend == "smtp":
        return SmtpEmailNotifier(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            sender=settings.smtp_from,
            use_tls=settings.smtp_use_tls,
        )
    return LoggingEmailNotifier()
