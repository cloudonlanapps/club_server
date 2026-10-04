"""Tests for #267 — the isolated email module (templates, senders, factory).

These exercise the module with no network: the console sender records into a
class-level outbox, and provider adapters are driven through monkeypatched sync
bodies to prove they never raise.
"""

import pytest

from club_server.mailer import templates
from club_server.mailer.config import email_settings as settings
from club_server.mailer.factory import build_sender
from club_server.mailer.providers import BrevoSender, ResendSender
from club_server.mailer.sender import (
    ConsoleEmailSender,
    EmailMessage,
    SendResult,
)


# --- templates -------------------------------------------------------------


def test_t1_forgot_password_uses_short_name_in_subject_full_in_header():
    subject, html, text = templates.forgot_password(
        club_name="Rink Hockey Club",
        club_short_name="RHC",
        first_name="Pat",
        new_password="Secret123",
        login_url="https://app/login",
    )
    # Short form in the subject, full name in the header.
    assert "RHC" in subject and "Rink Hockey Club" not in subject
    assert "Rink Hockey Club" in html
    assert "Secret123" in html and "Secret123" in text
    assert "https://app/login" in html
    assert "Pat" in html


def test_t2_admin_reset_mentions_administrator():
    subject, html, _ = templates.admin_reset(
        club_name="Rink Hockey Club",
        club_short_name="RHC",
        first_name="Pat",
        new_password="Secret123",
        login_url="https://app/login",
    )
    assert "administrator" in subject.lower() or "administrator" in html.lower()
    assert "RHC" in subject
    assert "Secret123" in html


def test_t3_account_approved():
    subject, html, text = templates.account_approved(
        club_name="Rink Hockey Club",
        club_short_name="RHC",
        first_name="Pat",
        login_url="https://app/login",
    )
    assert "approved" in subject.lower()
    assert "RHC" in subject
    assert "https://app/login" in html and "https://app/login" in text


def test_t4_broadcast_renders_markdown_and_keeps_subject():
    subject, html, text = templates.broadcast(
        club_name="Rink Hockey Club",
        club_short_name="RHC",
        email_subject="Practice cancelled",
        email_body="**No practice** tonight. See [details](https://x.test).",
    )
    assert subject == "Practice cancelled"
    # markdown rendered to HTML
    assert "<strong>No practice</strong>" in html
    assert '<a href="https://x.test">details</a>' in html
    assert "Rink Hockey Club" in html  # full name in banner header
    # plain-text part is the original markdown
    assert text == "**No practice** tonight. See [details](https://x.test)."


def test_t4_broadcast_passes_through_raw_html_in_markdown():
    _, html, _ = templates.broadcast(
        club_name="Rink Hockey Club",
        club_short_name="RHC",
        email_subject="S",
        email_body="<p>raw <b>html</b> kept</p>",
    )
    assert "<p>raw <b>html</b> kept</p>" in html


def test_template_escapes_name_to_prevent_injection():
    _, html, _ = templates.account_approved(
        club_name="Rink Hockey Club",
        club_short_name="RHC",
        first_name="<script>x</script>",
        login_url="https://app/login",
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_greeting_without_first_name():
    _, html, _ = templates.forgot_password(
        club_name="Rink Hockey Club",
        club_short_name="RHC",
        first_name=None,
        new_password="Secret123",
        login_url="https://app/login",
    )
    assert "Hi," in html


def test_banner_renders_logo_short_name_affiliation_and_theme(monkeypatch):
    # `email_logo_url` is derived (#311): the deployer configures a path
    # relative to the static mount and the absolute URL is built from
    # `server_base_url`, so the banner gets a link email clients can resolve.
    monkeypatch.setattr(settings, "server_base_url", "https://cdn")
    monkeypatch.setattr(settings, "email_logo_path", "logo.png")
    monkeypatch.setattr(settings, "email_affiliation", "Affiliated with X")
    monkeypatch.setattr(settings, "email_theme_color", "#E8772E")
    _, html, _ = templates.account_approved(
        club_name="Rink Hockey Club",
        club_short_name="RHC",
        first_name="Pat",
        login_url="https://app/login",
    )
    assert '<img src="https://cdn/static/logo.png"' in html
    assert "(RHC)" in html  # short name in the banner
    assert "#E8772E" in html  # theme colour used
    assert "Affiliated with X" in html


def test_banner_omits_the_affiliation_line_when_unset(monkeypatch):
    """An unset affiliation removes the line, rather than emitting an empty one.

    The affiliation is optional and several deployments have none. Rendering an
    empty element would put a blank italic gap under the club name in every
    email, which reads as a layout bug rather than an absent value.
    """
    monkeypatch.setattr(settings, "email_affiliation", "")
    _, html, _ = templates.account_approved(
        club_name="Rink Hockey Club",
        club_short_name="RHC",
        first_name="Pat",
        login_url="https://app/login",
    )
    # The affiliation is the only italic element in the banner.
    assert "font-style:italic" not in html
    # And the rest of the banner is untouched.
    assert "Rink Hockey Club" in html
    assert "(RHC)" in html


def test_banner_is_text_only_when_no_logo_configured(monkeypatch):
    monkeypatch.setattr(settings, "email_logo_path", "")
    _, html, _ = templates.account_approved(
        club_name="Rink Hockey Club",
        club_short_name="RHC",
        first_name="Pat",
        login_url="https://app/login",
    )
    assert "<img" not in html
    assert "Rink Hockey Club" in html  # name still shown


# --- console sender --------------------------------------------------------


@pytest.mark.asyncio
async def test_console_sender_records_and_succeeds():
    ConsoleEmailSender.clear()
    sender = ConsoleEmailSender()
    msg = EmailMessage(to="a@b.com", subject="Hi", html="<p>x</p>", text="x")
    result = await sender.send(msg)
    assert isinstance(result, SendResult)
    assert result.success and result.provider == "console"
    assert ConsoleEmailSender.outbox == [msg]


@pytest.mark.asyncio
async def test_console_outbox_shared_across_instances():
    ConsoleEmailSender.clear()
    await ConsoleEmailSender().send(EmailMessage(to="a@b.com", subject="1", html="x"))
    await ConsoleEmailSender().send(EmailMessage(to="c@d.com", subject="2", html="y"))
    assert [m.subject for m in ConsoleEmailSender.outbox] == ["1", "2"]


# --- factory ---------------------------------------------------------------


def test_factory_defaults_to_console():
    assert isinstance(build_sender("anything-unknown"), ConsoleEmailSender)


def test_factory_resend_without_key_falls_back_to_console(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", None)
    monkeypatch.setattr(settings, "email_from_transactional", "")
    assert isinstance(build_sender("resend"), ConsoleEmailSender)


def test_factory_resend_with_config_builds_resend(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", "re_key")
    monkeypatch.setattr(settings, "email_from_transactional", "no-reply@club.com")
    assert isinstance(build_sender("resend"), ResendSender)


def test_factory_brevo_with_config_builds_brevo(monkeypatch):
    monkeypatch.setattr(settings, "brevo_api_key", "xkey")
    monkeypatch.setattr(settings, "email_from_broadcast", "news@club.com")
    assert isinstance(build_sender("brevo"), BrevoSender)


# --- adapters never raise --------------------------------------------------


@pytest.mark.asyncio
async def test_resend_adapter_reports_failure_instead_of_raising(monkeypatch):
    sender = ResendSender("re_key", "no-reply@club.com")

    def boom(_msg):
        raise RuntimeError("api down")

    monkeypatch.setattr(sender, "_send_sync", boom)
    result = await sender.send(
        EmailMessage(to="a@b.com", subject="Hi", html="<p>x</p>")
    )
    assert result.success is False
    assert result.provider == "resend"
    assert "api down" in (result.error or "")


@pytest.mark.asyncio
async def test_resend_adapter_success(monkeypatch):
    sender = ResendSender("re_key", "no-reply@club.com")
    monkeypatch.setattr(sender, "_send_sync", lambda _msg: "msg_123")
    result = await sender.send(
        EmailMessage(to="a@b.com", subject="Hi", html="<p>x</p>")
    )
    assert result.success and result.message_id == "msg_123"


@pytest.mark.asyncio
async def test_brevo_adapter_reports_failure_instead_of_raising(monkeypatch):
    sender = BrevoSender("xkey", "news@club.com", "Rink FC")

    def boom(_msg):
        raise RuntimeError("brevo down")

    monkeypatch.setattr(sender, "_send_sync", boom)
    result = await sender.send(
        EmailMessage(to="a@b.com", subject="Hi", html="<p>x</p>")
    )
    assert result.success is False and "brevo down" in (result.error or "")
