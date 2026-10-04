"""``club_email_test`` — manual send harness for the email module.

Renders one of the four templates with placeholder data and sends it through a
chosen provider. Use this to verify real Resend/Brevo delivery against
configured credentials. It is the only entry point in this module; nothing here
is wired into the HTTP server.

Examples::

    club_email_test --provider console --template t1 --to me@example.com
    club_email_test --provider resend  --template t3 --to me@example.com
    club_email_test --provider brevo   --template t4 --to me@example.com \\
        --subject "Practice cancelled" --body "<p>No practice tonight.</p>"
"""

import argparse
import asyncio
import sys

from . import templates
from .config import email_settings as settings
from .factory import build_sender
from .sender import EmailMessage


def _render(template: str, subject: str | None, body: str | None):
    club = settings.club_name
    short = settings.club_short_name
    login = settings.login_url or "https://example.com/login"
    if template == "t1":
        return templates.forgot_password(
            club_name=club,
            club_short_name=short,
            first_name="Pat",
            new_password="Temp1234abcd",
            login_url=login,
        )
    if template == "t2":
        return templates.admin_reset(
            club_name=club,
            club_short_name=short,
            first_name="Pat",
            new_password="Temp1234abcd",
            login_url=login,
        )
    if template == "t3":
        return templates.account_approved(
            club_name=club,
            club_short_name=short,
            first_name="Pat",
            login_url=login,
        )
    if template == "t4":
        return templates.broadcast(
            club_name=club,
            club_short_name=short,
            email_subject=subject or "Test broadcast",
            email_body=body or "<p>This is a test broadcast.</p>",
        )
    raise ValueError(f"unknown template {template!r}")


async def _run(args: argparse.Namespace) -> int:
    subject, html, text = _render(args.template, args.subject, args.body)
    sender = build_sender(args.provider)
    print(f"Sending template {args.template} via '{sender.name}' to {args.to} ...")
    result = await sender.send(
        EmailMessage(to=args.to, subject=subject, html=html, text=text)
    )
    if result.success:
        print(f"OK (provider={result.provider} id={result.message_id})")
        return 0
    print(f"FAILED (provider={result.provider}): {result.error}", file=sys.stderr)
    return 1


def main() -> None:
    """CLI entry point for the email test harness."""
    parser = argparse.ArgumentParser(description="Send a test email.")
    _ = parser.add_argument(
        "--provider", choices=["console", "resend", "brevo"], default="console"
    )
    _ = parser.add_argument(
        "--template", choices=["t1", "t2", "t3", "t4"], required=True
    )
    _ = parser.add_argument("--to", required=True, help="Recipient email address")
    _ = parser.add_argument("--subject", help="Subject for the t4 broadcast template")
    _ = parser.add_argument("--body", help="HTML body for the t4 broadcast template")
    args = parser.parse_args()

    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
