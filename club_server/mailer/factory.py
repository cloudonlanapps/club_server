"""Sender selection from configuration.

Two purposes, independently switchable:

- ``transactional`` (auth mail) → ``settings.transactional_email_provider``
- ``broadcast`` (mass mail)      → ``settings.broadcast_email_provider``

A provider can be swapped purely via config without touching call sites. When a
provider is selected but its credentials are missing, we log a warning and fall
back to the console sender rather than crashing a request path — delivery is
best-effort everywhere.
"""

import logging

from .config import email_settings as settings
from .providers import BrevoSender, ResendSender
from .sender import ConsoleEmailSender, EmailSender

logger = logging.getLogger("club_server.mailer")


def _build_resend() -> EmailSender:
    if not settings.resend_api_key or not settings.email_from_transactional:
        logger.warning(
            "transactional provider 'resend' selected but resend_api_key / "
            "email_from_transactional not configured; using console sender"
        )
        return ConsoleEmailSender()
    return ResendSender(settings.resend_api_key, settings.email_from_transactional)


def _build_brevo() -> EmailSender:
    if not settings.brevo_api_key or not settings.email_from_broadcast:
        logger.warning(
            "broadcast provider 'brevo' selected but brevo_api_key / "
            "email_from_broadcast not configured; using console sender"
        )
        return ConsoleEmailSender()
    return BrevoSender(
        settings.brevo_api_key,
        settings.email_from_broadcast,
        settings.club_name,
    )


def build_sender(provider: str) -> EmailSender:
    """Construct a sender by provider name (``resend`` / ``brevo`` / other)."""
    if provider == "resend":
        return _build_resend()
    if provider == "brevo":
        return _build_brevo()
    return ConsoleEmailSender()


def get_transactional_sender() -> EmailSender:
    """Sender for 1-to-1 auth mail (password reset, admin reset, approval)."""
    return build_sender(settings.transactional_email_provider)


def get_broadcast_sender() -> EmailSender:
    """Sender for 1-to-many broadcast mail."""
    return build_sender(settings.broadcast_email_provider)
