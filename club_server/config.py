import os
import re
from typing import Annotated, Any, ClassVar

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Docker mounts secrets as files here, one per secret, named for the setting
# they carry. pydantic-settings reads a file whose name matches a field, so
# `secret_key` and `encryption_key` arrive without ever being an environment
# entry — and so out of `docker inspect` and /proc/<pid>/environ.
#
# Resolved at import rather than hardcoded so a checkout with no such directory
# (local dev, tests, CI) does not trip pydantic-settings' "directory does not
# exist" warning on every import.
#
# Environment still wins where both are present: pydantic-settings ranks env
# above file secrets. That is deliberate — a deployment can move one value at a
# time instead of all at once.
_SECRETS_DIR: str | None = "/run/secrets" if os.path.isdir("/run/secrets") else None

# A country calling code: one to three ASCII digits, written without "+".
_COUNTRY_CODE_PATTERN = re.compile(r"[0-9]{1,3}")


class Settings(BaseSettings):
    """Application settings, loaded from environment variables.

    Product identity — the CORS allowlist here, club naming and email branding
    in `mailer.config` — has no in-code default. The deploy conf supplies it
    per environment via the `EXTRA_ENV` passthrough, alongside the secrets, so
    an instance that omits a value fails at startup rather than silently
    serving under another club's identity. See issue #311.
    """

    # Database - required, no inline default
    database_url: str

    # JWT - required, no inline default
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # Environment
    environment: str = "development"

    # Filesystem locations - required, no inline default. These must be set
    # explicitly so a missing env var causes startup to fail loudly instead
    # of silently writing to the wrong directory. See issue #9.
    upload_dir: str
    static_dir: str

    # Browser origins permitted to call the API. Required, no default: a
    # hardcoded fallback here once pinned this server to one club's domain
    # while it served another, and a deployment that omitted the setting
    # inherited that silently. See issue #311.
    cors_allowed_origins: Annotated[list[str], NoDecode]

    max_image_upload_size_mb: int = 10
    max_pdf_upload_size_mb: int = 10
    max_video_upload_size_mb: int = 50
    # Encryption at rest (#150) — opt-in per upload via ``encrypt=true`` form
    # field. Server requires a base64-encoded 32-byte KEK in ``encryption_key``;
    # without one, encrypted POSTs and encrypted downloads return 503.
    encryption_key: str | None = None
    # Matches the plaintext image/PDF caps (#285): the limit is checked on the
    # raw upload bytes before webp conversion, so a lower value would reject
    # 5–10 MB originals once identity uploads flip to ``encrypt=true``.
    max_encrypted_upload_size_mb: int = 10
    media_convert_script: str = ""

    # Media link tables (#162) — generic per-owner caps. Tag-agnostic.
    media_max_links_per_owner_tag: int = 100
    media_max_tags_per_owner: int = 50

    # Credit system (#294) — optional per deployment. One club runs on
    # credits and another does not, from this one codebase, decided by the
    # deploy conf rather than at runtime. Defaults off so a deployment that
    # says nothing behaves exactly as it did before the subsystem existed:
    # the credit endpoints stay registered but answer 503
    # CREDIT_SYSTEM_DISABLED (R94), and the enrollment and attendance
    # integration points are inert.
    credit_system_enabled: bool = False

    # Evaluations (#302) — optional per deployment: one club removed
    # evaluations deliberately (#9), another wants them. Decided by the deploy conf rather
    # than at runtime, following the credit-system pattern above. Defaults
    # off so a deployment that says nothing behaves as it did before.
    evaluations_enabled: bool = False

    # Event Marketing (#410) — the extended commercial block on events (fees,
    # deadlines, offers…). One club wants it, another does not; decided by
    # the deploy conf like the two modules above, off by default.
    event_marketing_enabled: bool = False
    # Identity-document verification (#428) — one club verifies new members'
    # identity documents, another does not. On by default, which keeps the
    # flow of #142: a new user is ``registered`` until they upload a document
    # and submit for review. Off, registration goes straight to ``pending``.
    identity_verification_required: bool = True
    # Scheduling horizon (one-off R20a, R20b) — how far ahead a camp or a
    # one-off may be scheduled, in weeks. A programme is open-ended and exempt.
    # Configurable because it protects nothing but the cost of a conflict
    # report, and how far ahead a club plans is a fact about the club.
    scheduling_horizon_weeks: int = 52
    # Default country code (#15) — the club's country calling code, one to
    # three digits without "+". The apps store phone numbers in international
    # format and complete a number typed without a code with this one; which
    # country a club is in is a fact about the deployment. Unset by default.
    default_country_code: str | None = None

    # API
    api_v1_prefix: str = "/v1"

    model_config: ClassVar[SettingsConfigDict] = {
        "extra": "ignore",
        "secrets_dir": _SECRETS_DIR,
    }

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        """Accept a comma-separated string as well as a YAML list.

        The environment can only carry a string, and the deploy tooling has
        always passed `CORS_ALLOWED_ORIGINS` as a comma-separated list (or the
        single token `*`). `NoDecode` suppresses pydantic's JSON parsing so
        those plain values reach this validator intact.
        """
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("default_country_code", mode="before")
    @classmethod
    def _check_country_code(cls, value: Any) -> Any:
        """Accept one to three digits; treat an empty value as not set.

        A deploy conf writes a key it has no value for as an empty string, so
        that must mean "unset" rather than stop the server. Anything else that
        is not a calling code fails at startup, naming this setting.
        """
        if not isinstance(value, str):
            return value
        code = value.strip()
        if not code:
            return None
        if not _COUNTRY_CODE_PATTERN.fullmatch(code):
            raise ValueError(
                "DEFAULT_COUNTRY_CODE must be a country calling code of one to "
                'three digits without "+", e.g. 91'
            )
        return code

    @property
    def cors_allow_credentials(self) -> bool:
        """Whether to permit credentialed cross-origin requests.

        The CORS spec forbids pairing `Access-Control-Allow-Credentials: true`
        with a wildcard origin, so the wildcard forces credentials off.
        """
        return "*" not in self.cors_allowed_origins


settings = Settings()
