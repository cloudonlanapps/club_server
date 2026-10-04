"""Server-side, provider-agnostic email templates.

Each builder returns a ``(subject, html, text)`` tuple. Kept deliberately
dependency-free (plain string formatting + ``html.escape``) so there is no
templating engine to carry; the bodies are short and stable.

User-supplied values (names) are HTML-escaped. ``emailBody`` for the broadcast
wrapper (T4) is admin-authored and treated as trusted HTML.
"""

from html import escape

import markdown

from .config import email_settings

# The display names (club_name / club_short_name) are passed in by callers so
# the renderers stay unit-testable; the rest of the banner branding (logo URL,
# affiliation line, theme colour) is read from ``email_settings`` — the single
# source of truth — so call sites do not have to thread it through.


def _banner(club_name: str, club_short_name: str) -> str:
    """Branded header: logo (left) beside the full name, the short name in the
    theme colour, and the affiliation line — laid out with a table so it holds
    up across email clients and wraps rather than breaks on narrow screens.
    """
    safe_club = escape(club_name)
    safe_short = escape(club_short_name)
    theme = escape(email_settings.email_theme_color)
    logo_url = email_settings.email_logo_url
    affiliation = email_settings.email_affiliation

    logo_cell = ""
    if logo_url:
        logo_cell = (
            f'<td width="84" valign="top" style="padding-right:14px">'
            f'<img src="{escape(logo_url)}" alt="{safe_short} logo" width="76" '
            f'style="display:block;border:0;width:76px;height:auto"></td>'
        )

    affiliation_html = ""
    if affiliation:
        affiliation_html = (
            f'<div style="font-style:italic;font-size:12px;color:#555;'
            f'margin-top:4px">{escape(affiliation)}</div>'
        )

    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse"><tr>'
        f"{logo_cell}"
        '<td valign="middle" style="word-break:break-word;text-align:center">'
        f'<div style="font-size:18px;font-weight:bold;color:#1f2d3d;'
        f'text-transform:uppercase;line-height:1.2">{safe_club}</div>'
        f'<div style="font-size:16px;font-weight:bold;color:{theme};'
        f'margin-top:2px">({safe_short})</div>'
        f"{affiliation_html}"
        "</td></tr></table>"
        f'<hr style="border:none;border-top:2px solid {theme};margin:14px 0">'
    )


def _shell(club_name: str, club_short_name: str, body_html: str) -> str:
    """Full branded HTML shell shared by every template: banner + body +
    footer. The header shows the full ``club_name`` with the logo; the footer
    uses the short ``club_short_name``.
    """
    safe_short = escape(club_short_name)
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:600px;'
        'margin:0 auto;padding:0 12px">'
        f"{_banner(club_name, club_short_name)}"
        f"{body_html}"
        '<hr style="margin-top:24px;border:none;border-top:1px solid #ddd">'
        f'<p style="color:#888;font-size:12px">This is an automated message '
        f"from {safe_short}. Please do not reply.</p>"
        f"</div>"
    )


def _greeting(first_name: str | None) -> str:
    return f"Hi {escape(first_name)}" if first_name else "Hi"


def forgot_password(
    *,
    club_name: str,
    club_short_name: str,
    first_name: str | None,
    new_password: str,
    login_url: str,
) -> tuple[str, str, str]:
    """T1 — user-initiated forgot-password: a freshly generated password."""
    subject = f"Your {club_short_name} password has been reset"
    greet = _greeting(first_name)
    safe_pw = escape(new_password)
    safe_url = escape(login_url)
    html = _shell(
        club_name,
        club_short_name,
        f"<p>{greet},</p>"
        f"<p>We received a request to reset your password. Your temporary "
        f"password is:</p>"
        f'<p style="font-size:18px"><b>{safe_pw}</b></p>'
        f'<p>Log in at <a href="{safe_url}">{safe_url}</a> and change it right '
        f"away from your account settings. If you did not request this, please "
        f"contact us.</p>",
    )
    text = (
        f"{first_name or 'Hi'},\n\n"
        f"We received a request to reset your password.\n"
        f"Your temporary password is: {new_password}\n\n"
        f"Log in at {login_url} and change it right away.\n"
        f"If you did not request this, please contact us.\n"
    )
    return subject, html, text


def admin_reset(
    *,
    club_name: str,
    club_short_name: str,
    first_name: str | None,
    new_password: str,
    login_url: str,
) -> tuple[str, str, str]:
    """T2 — administrator-initiated reset: a freshly generated password."""
    subject = f"Your {club_short_name} password has been reset by an administrator"
    greet = _greeting(first_name)
    safe_pw = escape(new_password)
    safe_url = escape(login_url)
    html = _shell(
        club_name,
        club_short_name,
        f"<p>{greet},</p>"
        f"<p>An administrator has reset your password. Your temporary password "
        f"is:</p>"
        f'<p style="font-size:18px"><b>{safe_pw}</b></p>'
        f'<p>Please log in at <a href="{safe_url}">{safe_url}</a> and change it '
        f"immediately from your account settings.</p>",
    )
    text = (
        f"{first_name or 'Hi'},\n\n"
        f"An administrator has reset your password.\n"
        f"Your temporary password is: {new_password}\n\n"
        f"Please log in at {login_url} and change it immediately.\n"
    )
    return subject, html, text


def account_approved(
    *,
    club_name: str,
    club_short_name: str,
    first_name: str | None,
    login_url: str,
) -> tuple[str, str, str]:
    """T3 — account approved by an administrator."""
    subject = f"Your {club_short_name} account is approved"
    greet = _greeting(first_name)
    safe_url = escape(login_url)
    html = _shell(
        club_name,
        club_short_name,
        f"<p>{greet},</p>"
        f"<p>Good news — your account has been approved and is now active.</p>"
        f'<p>You can log in at <a href="{safe_url}">{safe_url}</a>.</p>',
    )
    text = (
        f"{first_name or 'Hi'},\n\n"
        f"Good news - your account has been approved and is now active.\n"
        f"You can log in at {login_url}.\n"
    )
    return subject, html, text


def inquiry_received(
    *,
    club_name: str,
    club_short_name: str,
    kind: str,
    name: str,
    email: str,
    phone: str | None,
    message: str,
    extra: dict | None,
) -> tuple[str, str, str]:
    """T5 — a contact-form or interest submission from the public website (#407)."""
    label = "contact request" if kind == "contact" else "interest registration"
    subject = f"New {label} for {club_short_name}: {name}"
    extras = "".join(
        f"<li>{escape(str(k))}: {escape(str(v))}</li>" for k, v in (extra or {}).items()
    )
    html = _shell(
        club_name,
        club_short_name,
        f"<p>A new {escape(label)} arrived from the website.</p>"
        f"<p><strong>Name:</strong> {escape(name)}<br>"
        f"<strong>Email:</strong> {escape(email)}<br>"
        f"<strong>Phone:</strong> {escape(phone or '-')}</p>"
        f"<p>{escape(message)}</p>" + (f"<ul>{extras}</ul>" if extras else ""),
    )
    text = (
        f"A new {label} arrived from the website.\n\n"
        f"Name: {name}\nEmail: {email}\nPhone: {phone or '-'}\n\n"
        f"{message}\n" + "".join(f"{k}: {v}\n" for k, v in (extra or {}).items())
    )
    return subject, html, text


def broadcast(
    *, club_name: str, club_short_name: str, email_subject: str, email_body: str
) -> tuple[str, str, str]:
    """T4 — branded wrapper around admin-authored broadcast content.

    ``email_body`` is authored by an admin as **markdown** and rendered to HTML
    here before being wrapped in the branded shell. Raw HTML embedded in the
    markdown passes through unchanged (broadcast authors are trusted admins;
    no sanitisation). The plain-text part is the original markdown, which is
    already human-readable.
    """
    body_html = markdown.markdown(email_body)
    html = _shell(club_name, club_short_name, body_html)
    return email_subject, html, email_body
