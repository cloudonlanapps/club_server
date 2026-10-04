"""Concrete provider adapters: Resend (transactional) and Brevo (broadcast).

Both vendor SDKs are synchronous, so every network call is offloaded to a
worker thread via :func:`asyncio.to_thread` to keep the event loop free.

Adapters never raise: any provider/transport error is captured and returned as
``SendResult(success=False, ...)`` so call sites can treat delivery as
best-effort.
"""

import asyncio
import logging

from .sender import EmailMessage, EmailSender, SendResult

logger = logging.getLogger("club_server.mailer")


class ResendSender(EmailSender):
    """Transactional sender backed by the Resend API."""

    name = "resend"

    def __init__(self, api_key: str, from_email: str):
        self._api_key = api_key
        self._from = from_email

    def _send_sync(self, message: EmailMessage) -> str | None:
        import resend

        resend.api_key = self._api_key
        params: dict[str, object] = {
            "from": self._from,
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
        }
        if message.text:
            params["text"] = message.text
        result = resend.Emails.send(params)  # pyright: ignore[reportArgumentType]
        return result.get("id") if isinstance(result, dict) else None

    async def send(self, message: EmailMessage) -> SendResult:
        try:
            message_id = await asyncio.to_thread(self._send_sync, message)
            return SendResult(success=True, provider=self.name, message_id=message_id)
        except Exception as exc:  # noqa: BLE001 — adapters must not raise
            logger.warning("resend send failed to=%s: %s", message.to, exc)
            return SendResult(success=False, provider=self.name, error=str(exc))


class BrevoSender(EmailSender):
    """Broadcast sender backed by the Brevo (sib-api-v3-sdk) API."""

    name = "brevo"

    def __init__(self, api_key: str, from_email: str, from_name: str):
        self._api_key = api_key
        self._from_email = from_email
        self._from_name = from_name

    def _send_sync(self, message: EmailMessage) -> str | None:
        import sib_api_v3_sdk

        config = sib_api_v3_sdk.Configuration()
        config.api_key["api-key"] = self._api_key
        api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(config))
        payload = sib_api_v3_sdk.SendSmtpEmail(
            sender={"email": self._from_email, "name": self._from_name},
            to=[{"email": message.to}],
            subject=message.subject,
            html_content=message.html,
            text_content=message.text or None,
        )
        resp = api.send_transac_email(payload)
        return getattr(resp, "message_id", None)

    async def send(self, message: EmailMessage) -> SendResult:
        try:
            message_id = await asyncio.to_thread(self._send_sync, message)
            return SendResult(success=True, provider=self.name, message_id=message_id)
        except Exception as exc:  # noqa: BLE001 — adapters must not raise
            logger.warning("brevo send failed to=%s: %s", message.to, exc)
            return SendResult(success=False, provider=self.name, error=str(exc))
