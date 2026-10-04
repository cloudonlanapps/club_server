"""Provider-agnostic email sender interface and the console (no-op) sender."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger("club_server.mailer")


@dataclass(frozen=True)
class EmailMessage:
    """A fully rendered email, ready to hand to a provider adapter."""

    to: str
    subject: str
    html: str
    text: str | None = None


@dataclass(frozen=True)
class SendResult:
    """Outcome of a single send attempt.

    ``success`` is the only field call sites should branch on; ``message_id``
    and ``error`` are diagnostic and flow into audit records.
    """

    success: bool
    provider: str
    message_id: str | None = None
    error: str | None = None


class EmailSender(ABC):
    """Abstract transport. Adapters implement :meth:`send` only."""

    name: str = "abstract"

    @abstractmethod
    async def send(self, message: EmailMessage) -> SendResult:
        """Send a single rendered message. Must never raise — failures are
        reported via ``SendResult.success == False`` so callers can treat
        delivery as best-effort without wrapping every call in try/except."""
        raise NotImplementedError


class ConsoleEmailSender(EmailSender):
    """Dev/test sender. Logs the rendered message and records it in a
    class-level outbox; performs no network I/O.

    Tests assert against :attr:`outbox`. Call :meth:`clear` in fixtures to
    isolate runs.
    """

    name = "console"

    #: Every message "sent" by any ConsoleEmailSender instance, in order.
    #: Shared at the class level so tests can inspect sends regardless of which
    #: instance the factory handed to the service under test.
    outbox: list[EmailMessage] = []

    @classmethod
    def clear(cls) -> None:
        cls.outbox.clear()

    async def send(self, message: EmailMessage) -> SendResult:
        type(self).outbox.append(message)
        logger.info(
            "console-email to=%s subject=%r (not actually sent)",
            message.to,
            message.subject,
        )
        return SendResult(success=True, provider=self.name, message_id="console")
