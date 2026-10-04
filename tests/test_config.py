"""
Tests for `club_server.config.Settings`.

Covers issue #9: STATIC_DIR / UPLOAD_DIR must be required (no in-code default),
so a missing env var causes startup to fail loudly instead of silently writing
to the wrong directory.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from club_server.config import Settings

# Every Settings field that has no in-code default must be either provided
# explicitly or present in the OS env. Tests that want to verify "missing X
# raises" must clear *all* required env vars first, then put the others back,
# so the field under test is the only one missing.
_REQUIRED_FIELDS = (
    "DATABASE_URL",
    "SECRET_KEY",
    "STATIC_DIR",
    "UPLOAD_DIR",
    # Also has no in-code default as of #311. Set explicitly here rather than
    # letting the fixture config file supply it, so these tests stay
    # independent of what `conftest.py` exports.
    "CORS_ALLOWED_ORIGINS",
)

_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
    "SECRET_KEY": "test-secret",
    "STATIC_DIR": "/tmp/club_server_test_static",
    "UPLOAD_DIR": "/tmp/club_server_test_uploads",
    "CORS_ALLOWED_ORIGINS": "*",
}


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Clear all required env vars.

    Returns the monkeypatch fixture so tests can re-add the vars they want.
    Tests build the env explicitly with `monkeypatch.setenv`, then call
    `_make()` to construct.
    """
    for var in _REQUIRED_FIELDS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _make(**overrides: str) -> Settings:
    """Construct Settings() from the current process environment."""
    return Settings()


def test_settings_constructs_when_all_required_env_present(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    """Baseline: with every required field provided via env, Settings() succeeds."""
    for var, val in _DEFAULTS.items():
        isolated_env.setenv(var, val)
    settings = _make()
    assert settings.static_dir == _DEFAULTS["STATIC_DIR"]
    assert settings.upload_dir == _DEFAULTS["UPLOAD_DIR"]
    assert settings.database_url == _DEFAULTS["DATABASE_URL"]
    assert settings.secret_key == _DEFAULTS["SECRET_KEY"]


def test_settings_raises_when_static_dir_missing(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    """STATIC_DIR has no in-code default; omitting it must raise."""
    for var, val in _DEFAULTS.items():
        if var == "STATIC_DIR":
            continue
        isolated_env.setenv(var, val)
    with pytest.raises(ValidationError) as exc_info:
        _make()
    assert "static_dir" in str(exc_info.value).lower()


def test_settings_raises_when_upload_dir_missing(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    """UPLOAD_DIR has no in-code default; omitting it must raise."""
    for var, val in _DEFAULTS.items():
        if var == "UPLOAD_DIR":
            continue
        isolated_env.setenv(var, val)
    with pytest.raises(ValidationError) as exc_info:
        _make()
    assert "upload_dir" in str(exc_info.value).lower()


def test_settings_raises_when_database_url_missing(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    """Regression guard: existing required field must stay required."""
    for var, val in _DEFAULTS.items():
        if var == "DATABASE_URL":
            continue
        isolated_env.setenv(var, val)
    with pytest.raises(ValidationError):
        _make()


def test_settings_raises_when_secret_key_missing(
    isolated_env: pytest.MonkeyPatch,
) -> None:
    """Regression guard: existing required field must stay required."""
    for var, val in _DEFAULTS.items():
        if var == "SECRET_KEY":
            continue
        isolated_env.setenv(var, val)
    with pytest.raises(ValidationError):
        _make()


def test_static_dir_value_is_honoured(
    isolated_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Settings.static_dir must reflect whatever STATIC_DIR is set to."""
    for var, val in _DEFAULTS.items():
        isolated_env.setenv(var, val)
    custom = str(tmp_path / "custom-static")
    isolated_env.setenv("STATIC_DIR", custom)
    settings = _make()
    assert settings.static_dir == custom


def test_upload_dir_value_is_honoured(
    isolated_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Settings.upload_dir must reflect whatever UPLOAD_DIR is set to."""
    for var, val in _DEFAULTS.items():
        isolated_env.setenv(var, val)
    custom = str(tmp_path / "custom-uploads")
    isolated_env.setenv("UPLOAD_DIR", custom)
    settings = _make()
    assert settings.upload_dir == custom


def test_main_module_static_dir_is_derived_from_settings() -> None:
    """`club_server.main.STATIC_DIR` must come from settings, not a hardcoded path.

    This is the actual bug from issue #9: previously main.py used
    `Path(__file__).parent.parent / "static"`, ignoring STATIC_DIR entirely.
    The variable must now resolve to whatever `settings.static_dir` says.
    """
    from club_server import main as main_module
    from club_server.config import settings as live_settings

    expected = Path(live_settings.static_dir).resolve()
    assert main_module.STATIC_DIR == expected
    # And it must actually exist on disk, since main.py is responsible for
    # creating it on startup.
    assert main_module.STATIC_DIR.exists()
    assert main_module.STATIC_DIR.is_dir()
