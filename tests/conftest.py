import asyncio
import os
import tempfile
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# The test suite runs against a local PostgreSQL instance started via
# start_db.sh in the repo root (see tests/README.md). TEST_DB_PORT names the cluster;
# the 5435 default is only a fallback for hand runs.
TEST_DB_USER = "myclub"
TEST_DB_PASSWORD = "devpass"  # noqa: S105 — dev-only, matches start_db.sh
TEST_DB_NAME = "myclub"
TEST_DB_HOST = "localhost"
TEST_DB_PORT = os.environ.get("TEST_DB_PORT", "5435")

# Guard: never run tests against the development database (default port 5437).
# The dev instance is started via start_db.sh which defaults to 5437.
# Tests drop and recreate all tables per function, which would destroy dev data.
_DEV_DB_PORT = "5437"
if TEST_DB_PORT == _DEV_DB_PORT:
    raise RuntimeError(
        f"TEST_DB_PORT={_DEV_DB_PORT} matches the development database port. "
        f"Tests drop all tables and would destroy dev data. "
        f"Run the suite via just tests::auto, or start a separate cluster: start_db.sh --db-port 5450 --data-dir /tmp/club_server_test_run_5450"
    )
TEST_DATABASE_URL = (
    f"postgresql+asyncpg://{TEST_DB_USER}:{TEST_DB_PASSWORD}"
    f"@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
)

# Force the test DB URL and other required settings into the environment
# *before* any club_server import, because Settings() is instantiated at
# module load time and the test process must never accidentally connect to
# a developer's real DATABASE_URL from a shell env or .env file.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("SECRET_KEY", "test-secret-key")

_TEST_STATIC_DIR = os.path.join(tempfile.gettempdir(), "club_server_test_static")
_TEST_UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "club_server_test_uploads")
os.environ.setdefault("STATIC_DIR", _TEST_STATIC_DIR)
os.environ.setdefault("UPLOAD_DIR", _TEST_UPLOAD_DIR)

# Product identity (club naming, CORS, email branding) has no in-code default
# — see issue #311 — so the suite supplies it here. In a real deployment these
# come from the deploy conf's EXTRA_ENV passthrough; the values below are
# deliberately fictional so a test can never pass by inheriting a production
# one. `setdefault` so an individual test run can override any of them.
_TEST_IDENTITY = {
    "CORS_ALLOWED_ORIGINS": "*",
    "CLUB_NAME": "Test Club",
    "CLUB_SHORT_NAME": "TC",
    "LOGIN_URL": "https://member.test.invalid",
    "SERVER_BASE_URL": "https://api.test.invalid",
    "EMAIL_FROM_TRANSACTIONAL": "admin@member.test.invalid",
    "EMAIL_FROM_BROADCAST": "noreply@test.invalid",
    "EMAIL_LOGO_PATH": "images/logo.png",
    "EMAIL_AFFILIATION": "Test affiliation line",
    "EMAIL_THEME_COLOR": "#E8772E",
}
for _key, _value in _TEST_IDENTITY.items():
    os.environ.setdefault(_key, _value)

# club_server imports must come after the os.environ setup above: Settings() is
# instantiated at module-load time and must read the test DATABASE_URL, never a
# developer's real one.
from club_server.db.base import Base  # noqa: E402
from club_server.dependencies import get_db  # noqa: E402
from club_server.main import app  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def test_engine():
    """Create a test database engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # The media_in_use view (#162) is created via raw SQL by the
        # alembic migration; replicate it here so tests can query it.
        from sqlalchemy import text as _text

        await conn.execute(
            _text(
                "CREATE VIEW media_in_use AS "
                "SELECT media_uuid, 'user'::text  AS owner_type, username::text  AS owner_id, tag, metadata_value, created_at, updated_at FROM user_media "
                "UNION ALL "
                "SELECT media_uuid, 'event'::text AS owner_type, event_id::text  AS owner_id, tag, metadata_value, created_at, updated_at FROM event_media "
                "UNION ALL "
                "SELECT media_uuid, 'group'::text AS owner_type, group_id::text  AS owner_id, tag, metadata_value, created_at, updated_at FROM group_media "
                "UNION ALL "
                "SELECT media_uuid, 'venue'::text AS owner_type, venue_id::text  AS owner_id, tag, metadata_value, created_at, updated_at FROM venue_media "
                "UNION ALL "
                "SELECT media_uuid, 'evaluation'::text AS owner_type, evaluation_id::text AS owner_id, tag, metadata_value, created_at, updated_at FROM evaluation_media"
            )
        )

    yield engine

    async with engine.begin() as conn:
        from sqlalchemy import text as _text

        await conn.execute(_text("DROP VIEW IF EXISTS media_in_use"))
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    async_session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_maker() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create a test HTTP client."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        try:
            yield db_session
            await db_session.commit()
            db_session.expire_all()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def credit_enabled(monkeypatch: pytest.MonkeyPatch):
    """Turn the optional credit system on for one test (#294, R92).

    The subsystem is off by default so that a deployment which says nothing
    behaves as it did before it existed — which is also what every test
    outside the credit suite exercises. Credit tests opt in with
    ``pytestmark = pytest.mark.usefixtures("credit_enabled")``.
    """
    from club_server.config import settings as _settings

    monkeypatch.setattr(_settings, "credit_system_enabled", True)
    return None


@pytest.fixture(scope="function")
def event_marketing_enabled(monkeypatch: pytest.MonkeyPatch):
    """Turn the optional event-marketing module on for one test (#410, marketing R5).

    Off by default, like credit and evaluations; the marketing suite opts in
    with ``pytestmark = pytest.mark.usefixtures("event_marketing_enabled")``.
    """
    from club_server.config import settings as _settings

    monkeypatch.setattr(_settings, "event_marketing_enabled", True)
    return None


@pytest.fixture(scope="function")
def identity_verification_off(monkeypatch: pytest.MonkeyPatch):
    """Turn identity-document verification off for one test (#428).

    On by default: a deployment that says nothing keeps the upload and
    submit-for-review step, as before the setting existed.
    """
    from club_server.config import settings as _settings

    monkeypatch.setattr(_settings, "identity_verification_required", False)
    return None


@pytest.fixture(scope="function")
def evaluations_enabled(monkeypatch: pytest.MonkeyPatch):
    """Turn the optional evaluations module on for one test (#302, R61).

    Off by default for the same reason the credit system is: a deployment
    that says nothing behaves as it did before the module existed. The
    evaluation suites opt in with
    ``pytestmark = pytest.mark.usefixtures("evaluations_enabled")``.
    """
    from club_server.config import settings as _settings

    monkeypatch.setattr(_settings, "evaluations_enabled", True)
    return None
