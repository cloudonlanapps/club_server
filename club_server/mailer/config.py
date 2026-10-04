"""Email module settings — the single source of truth for email configuration.

Kept here (not in the app-wide ``club_server.config``) so the mailer and its
``club_email_test`` CLI can run without a database URL, secret key, or upload
dirs configured.

Values come from environment variables, which the deploy conf supplies per
environment through its ``EXTRA_ENV`` passthrough: literals for the identity
below, ``pass`` lookups for the API keys. Nothing here has a club-specific
default — a deployment that fails to supply its identity must refuse to start
rather than send mail wearing another club's name from another club's domain
(issue #311).

All email-facing copy and configuration is sourced from this one object —
templates take the names as parameters, and every call site reads them here.
"""

import os
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved here rather than imported from club_server.config, which builds its
# own settings singleton at import time — importing it would make this module
# unusable unless the whole application config is valid, which is not true in
# the mailer CLI or in a test that only cares about email.
_SECRETS_DIR: str | None = "/run/secrets" if os.path.isdir("/run/secrets") else None


class EmailSettings(BaseSettings):
    """Email + club-identity settings."""

    # Two independent providers. "console" writes to the log without touching
    # the network, which is what dev and test want; deployed environments set
    # "resend" (transactional) / "brevo" (broadcast). When a provider is
    # selected but its key/from-address is unset, the factory falls back to the
    # console sender and logs a warning.
    transactional_email_provider: str = "console"
    broadcast_email_provider: str = "console"

    # Secrets, resolved from the password store by the deploy tooling and
    # passed through the process environment; never written to disk.
    resend_api_key: str | None = None
    brevo_api_key: str | None = None

    # Sending addresses must be on a domain verified with the respective
    # provider.
    email_from_transactional: str
    email_from_broadcast: str

    # club_name is the full name shown in the email header banner;
    # club_short_name is the abbreviation rendered in the theme colour beneath
    # it, and used in subject lines and the footer.
    club_name: str
    club_short_name: str

    # The link a member clicks in email to sign in. Passed through as-is —
    # nothing appends a path to it — and read nowhere else in the server.
    login_url: str

    # Where this server is reached from outside. Used only to make the email
    # banner logo an absolute URL, since email clients cannot resolve a relative
    # path; unread when email_logo_path is empty.
    server_base_url: str

    # Email banner branding. ``email_logo_path`` is relative to the static
    # mount (e.g. ``images/logo.png``) and is joined against ``server_base_url``
    # by ``email_logo_url`` below; email clients cannot resolve relative paths,
    # so the absolute URL has to be built here. An empty path renders the
    # banner text-only.
    email_logo_path: str = ""
    email_affiliation: str = ""
    email_theme_color: str

    # Provider API keys are secrets; see the note on _SECRETS_DIR.
    model_config: ClassVar[SettingsConfigDict] = {
        "extra": "ignore",
        "secrets_dir": _SECRETS_DIR,
    }

    @property
    def email_logo_url(self) -> str:
        """Absolute, publicly reachable URL of the banner logo.

        Empty when no logo path is configured, which the banner renderer
        treats as "text-only".
        """
        if not self.email_logo_path:
            return ""
        return f"{self.server_base_url.rstrip('/')}/static/{self.email_logo_path.lstrip('/')}"


email_settings = EmailSettings()
