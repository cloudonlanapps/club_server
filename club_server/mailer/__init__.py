"""Isolated email-sending module.

Two independent providers behind one switchable ``EmailSender`` interface:

- **Resend** for transactional / auth mail (1-to-1).
- **Brevo** for broadcast mail (1-to-many).

Templates are rendered server-side to ``{subject, html, text}`` (see
``templates``) and handed to a deliberately dumb adapter. Provider hosted-
template editors are intentionally not used so a provider swap is a single
config change.

This package exposes no HTTP endpoints — it is pure backend integration plus
the ``club_email_test`` CLI harness (see ``cli``).
"""

from .factory import get_broadcast_sender, get_transactional_sender
from .sender import (
    ConsoleEmailSender,
    EmailMessage,
    EmailSender,
    SendResult,
)

__all__ = [
    "ConsoleEmailSender",
    "EmailMessage",
    "EmailSender",
    "SendResult",
    "get_broadcast_sender",
    "get_transactional_sender",
]
