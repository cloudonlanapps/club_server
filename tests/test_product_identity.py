"""Tests for per-deployment product identity (issue #311).

Club naming, public URLs, the CORS allowlist and email branding have no
in-code default. Each deployment supplies them through the environment — in
practice from the deploy conf's ``EXTRA_ENV`` passthrough, which carries these
as literals alongside the ``pass``-resolved secrets.

The point of the issue is the failure mode: an instance that omits a value
must refuse to start, rather than starting up wearing another club's identity.
These tests pin that, plus the two values the server derives rather than reads.
"""

import os

import pytest
from pydantic import ValidationError

from club_server.config import Settings
from club_server.mailer.config import EmailSettings

# Secrets and per-instance paths. Kept separate from the identity fields under
# test so a test that clears identity still has enough to construct Settings.
_SECRET_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
    "SECRET_KEY": "test-secret",
    "STATIC_DIR": "/tmp/club_server_test_static",
    "UPLOAD_DIR": "/tmp/club_server_test_uploads",
}

# Everything a deployment must supply. `conftest.py` sets these for the suite
# at large; the fixture below clears them so each test states what it needs.
_IDENTITY_ENV = {
    "CORS_ALLOWED_ORIGINS": "https://member.example.test,https://www.example.test",
    "CLUB_NAME": "Fixture Club",
    "CLUB_SHORT_NAME": "FC",
    "LOGIN_URL": "https://member.example.test",
    "SERVER_BASE_URL": "https://api.example.test",
    "EMAIL_FROM_TRANSACTIONAL": "admin@member.example.test",
    "EMAIL_FROM_BROADCAST": "noreply@example.test",
    "EMAIL_LOGO_PATH": "images/logo.png",
    "EMAIL_AFFILIATION": "Fixture affiliation line",
    "EMAIL_THEME_COLOR": "#123456",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Environment with secrets present and every identity variable cleared.

    Leaves each test to set exactly the identity it is asserting about, so a
    passing test can never be relying on what `conftest.py` happens to export.
    """
    for var in _IDENTITY_ENV:
        monkeypatch.delenv(var, raising=False)
    for var, value in _SECRET_ENV.items():
        monkeypatch.setenv(var, value)
    return monkeypatch


def _set_identity(env: pytest.MonkeyPatch, **overrides: str) -> None:
    """Apply the full identity set, with any per-test overrides on top."""
    for var, value in {**_IDENTITY_ENV, **overrides}.items():
        env.setenv(var, value)


# ── Identity is read from the environment ─────────────────────────────────────


def test_email_settings_read_club_identity(clean_env: pytest.MonkeyPatch) -> None:
    """Every club-identity field comes from its environment variable."""
    _set_identity(clean_env)
    settings = EmailSettings()
    assert settings.club_name == "Fixture Club"
    assert settings.club_short_name == "FC"
    assert settings.login_url == "https://member.example.test"
    assert settings.server_base_url == "https://api.example.test"
    assert settings.email_from_transactional == "admin@member.example.test"
    assert settings.email_from_broadcast == "noreply@example.test"
    assert settings.email_affiliation == "Fixture affiliation line"
    assert settings.email_theme_color == "#123456"


# ── Loud failure when identity is missing ─────────────────────────────────────


def test_missing_club_identity_raises(clean_env: pytest.MonkeyPatch) -> None:
    """With no identity supplied, EmailSettings must refuse to construct.

    This is the whole point of the issue: a deployment that forgets its
    identity must fail at startup rather than silently sending mail as another
    club. Mirrors the #9 treatment of STATIC_DIR / UPLOAD_DIR.
    """
    with pytest.raises(ValidationError) as exc_info:
        _ = EmailSettings()
    message = str(exc_info.value).lower()
    assert "club_name" in message


@pytest.mark.parametrize(
    "missing",
    [
        "CLUB_NAME",
        "CLUB_SHORT_NAME",
        "LOGIN_URL",
        "SERVER_BASE_URL",
        "EMAIL_FROM_TRANSACTIONAL",
        "EMAIL_FROM_BROADCAST",
        "EMAIL_THEME_COLOR",
    ],
)
def test_each_identity_field_is_individually_required(
    clean_env: pytest.MonkeyPatch, missing: str
) -> None:
    """Omitting any one identity variable must raise, naming that field.

    Parametrised per variable rather than asserted in a loop, so a field that
    quietly regains a default is reported on its own rather than hidden behind
    whichever field happens to fail first.
    """
    _set_identity(clean_env)
    clean_env.delenv(missing)
    with pytest.raises(ValidationError) as exc_info:
        _ = EmailSettings()
    assert missing.lower() in str(exc_info.value).lower()


def test_missing_cors_origins_raises(clean_env: pytest.MonkeyPatch) -> None:
    """The CORS allowlist has no in-code default and must be supplied.

    The removed fallback hardcoded one club's domain on a server that served
    another, and any deployment omitting the setting inherited it silently.
    """
    with pytest.raises(ValidationError) as exc_info:
        _ = Settings()
    assert "cors_allowed_origins" in str(exc_info.value).lower()


# ── CORS parsing and the credentials rule ─────────────────────────────────────


def test_cors_origins_parse_from_comma_separated_string(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """The deploy tooling passes origins as one comma-separated string.

    `deploy.sh` builds `CORS_ALLOWED_ORIGINS` by prefixing each entry of the
    conf's `<env>_ALLOWED_WEBSITES` with `https://` and joining on commas, so
    that exact shape has to parse into a list.
    """
    _set_identity(clean_env)
    settings = Settings()
    assert settings.cors_allowed_origins == [
        "https://member.example.test",
        "https://www.example.test",
    ]


def test_cors_origins_tolerate_surrounding_whitespace(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """Spaces around commas must not become part of an origin."""
    _set_identity(clean_env, CORS_ALLOWED_ORIGINS="https://a.test , https://b.test")
    assert Settings().cors_allowed_origins == ["https://a.test", "https://b.test"]


def test_wildcard_origin_disables_credentials(clean_env: pytest.MonkeyPatch) -> None:
    """`*` and `allow_credentials=True` are mutually exclusive per the CORS spec.

    Previously an if-chain in `main.py`; now a property on Settings so the rule
    is testable without importing the app.
    """
    _set_identity(clean_env, CORS_ALLOWED_ORIGINS="*")
    settings = Settings()
    assert settings.cors_allowed_origins == ["*"]
    assert settings.cors_allow_credentials is False


def test_explicit_origins_enable_credentials(clean_env: pytest.MonkeyPatch) -> None:
    """An explicit allowlist permits credentialed requests."""
    _set_identity(clean_env)
    assert Settings().cors_allow_credentials is True


# ── Derived values ────────────────────────────────────────────────────────────


def test_logo_url_is_built_from_server_base_url_and_path(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """`EMAIL_LOGO_PATH` is relative to the static mount and joined server-side.

    Email clients cannot resolve relative paths, so the absolute URL must be
    built here. It joins against `server_base_url`, not `login_url` — the
    logo is served from the API host, which differs from the member site.
    """
    _set_identity(clean_env)
    settings = EmailSettings()
    assert settings.email_logo_url == "https://api.example.test/static/images/logo.png"


def test_logo_url_tolerates_slashes_on_either_side(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """A trailing slash on the base or a leading one on the path must not double up."""
    _set_identity(
        clean_env,
        SERVER_BASE_URL="https://api.example.test/",
        EMAIL_LOGO_PATH="/images/logo.png",
    )
    assert (
        EmailSettings().email_logo_url
        == "https://api.example.test/static/images/logo.png"
    )


def test_logo_url_is_empty_when_path_omitted(clean_env: pytest.MonkeyPatch) -> None:
    """An empty logo path yields no URL, so the banner renders text-only."""
    _set_identity(clean_env, EMAIL_LOGO_PATH="")
    assert EmailSettings().email_logo_url == ""


# ── No club-specific values remain in code ────────────────────────────────────

# Substrings that mark a value as belonging to a specific club rather than to
# the product. Compared against a normalised form of the default (letters and
# digits only, lowercased) so prose and domain spellings are both caught:
# "Example Rink Club, North" and "examplerinkclub.org" reduce to the same
# tokens.
#
# The tokens name real clubs, so they are not kept here: a deployment's
# maintainers set CLUB_IDENTITY_TOKENS (comma-separated) where they run the
# suite. Without it, the check below is skipped.
_CLUB_SPECIFIC_TOKENS = tuple(
    token.strip().lower()
    for token in os.environ.get("CLUB_IDENTITY_TOKENS", "").split(",")
    if token.strip()
)


def _normalise(value: str) -> str:
    """Reduce a string to lowercase alphanumerics for substring matching."""
    return "".join(char for char in value.lower() if char.isalnum())


def test_normalise_collapses_prose_and_domain_spellings() -> None:
    """Guard the guard: the matcher must catch prose, not just domains.

    The first version of the test below compared against the raw string and so
    missed a club's name written as prose — the punctuation and spaces meant it
    did not contain the domain form. Normalising first is what makes the check
    meaningful, so that behaviour is pinned here.
    """
    assert "examplerink" in _normalise("Example Rink Club, North")
    assert "north" in _normalise("Example Rink Club, North")
    assert "erc9" in _normalise("https://www.erc9examplerink.org")
    assert "erc" in _normalise("(ERC)")


@pytest.mark.skipif(
    not _CLUB_SPECIFIC_TOKENS,
    reason="CLUB_IDENTITY_TOKENS is not set: no club names to check against",
)
def test_no_product_defaults_remain_in_settings_classes() -> None:
    """No field may default to a real club's name, domain, or branding.

    A default is exactly what let a misconfigured deployment start up wearing
    the wrong club's identity. Asserted over the model fields rather than by
    grepping, so a value reintroduced anywhere is caught.
    """
    for model in (Settings, EmailSettings):
        for name, field in model.model_fields.items():
            default = field.default
            if not isinstance(default, str):
                continue
            normalised = _normalise(default)
            for token in _CLUB_SPECIFIC_TOKENS:
                assert token not in normalised, (
                    f"{model.__name__}.{name} still defaults to a "
                    f"club-specific value: {default!r}"
                )
