"""The alembic revision graph is well-formed.

The rest of the suite cannot catch a broken migration: ``conftest.py`` builds
tables with ``Base.metadata.create_all``, so migrations are never executed by
tests and a fully green run says nothing about whether the server can start.

A hand-written migration once reused an existing revision id and pointed at a
node that was not the head. Both were invisible until deploy, where alembic
reports the duplicate as a *cycle* — an error that never names the real
problem, on a server with the old containers already stopped.

These read the graph through alembic's own ScriptDirectory rather than by
parsing the files, so they agree with what the server will do at startup.
They need no database and run in milliseconds.
"""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def script() -> ScriptDirectory:
    """The migration graph as alembic itself reads it.

    Constructing this is the test: a duplicate revision id or a cycle raises
    ``CycleDetected`` here, which is precisely the failure that reached a
    live deploy.
    """
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_should_have_exactly_one_head(script: ScriptDirectory):
    """Two heads means two migrations claim the same parent, and
    ``alembic upgrade head`` becomes ambiguous."""
    heads = script.get_heads()

    assert len(heads) == 1, f"expected one head, found {sorted(heads)}"


def test_should_have_exactly_one_base(script: ScriptDirectory):
    """Every migration chains back to a single starting point."""
    bases = script.get_bases()

    assert len(bases) == 1, f"expected one base, found {sorted(bases)}"


def test_should_walk_the_whole_chain_without_a_break(script: ScriptDirectory):
    """Every revision is reachable from the head.

    A ``down_revision`` naming a revision that does not exist would strand
    everything below it; walking from the head proves the chain is intact.
    """
    walked = {rev.revision for rev in script.walk_revisions()}
    declared = {rev.revision for rev in script.get_revisions("heads")}

    assert declared <= walked
    assert len(walked) == len(list(script.walk_revisions()))


# --- alembic depends on one variable, not on Settings (#420) -----------------
#
# ``alembic/env.py`` used to build the whole ``Settings`` object to read one
# field, so running a migration needed five variables and a script to export
# them. It now reads ``DATABASE_URL`` itself; the data migrations that touch
# the uploads directory read ``UPLOAD_DIR`` the same way.


def test_should_run_alembic_with_only_database_url_set():
    """``alembic history`` with an environment holding nothing but
    ``DATABASE_URL`` (and PATH). Loading env.py is the test: it imports the
    models, and if anything on that path constructs ``Settings`` the process
    dies with a pydantic ``Field required`` error."""
    import os
    import subprocess
    import sys

    env = {
        "PATH": os.environ.get("PATH", ""),
        "DATABASE_URL": "postgresql+asyncpg://x:x@localhost:1/x",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "history"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Initial schema" in proc.stdout


def test_should_prefer_database_url_from_env_over_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """The environment wins where both exist, as it does for ``Settings``."""
    from club_server.db.url import database_url_from_env

    _ = (tmp_path / "database_url").write_text("postgresql+asyncpg://file\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://env")
    assert database_url_from_env(tmp_path) == "postgresql+asyncpg://env"


def test_should_read_database_url_from_secret_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A container deployment delivers the URL as a secret file and never
    exports it. Reading only the environment made every container deploy
    fail at ``alembic upgrade head``."""
    from club_server.db.url import database_url_from_env

    _ = (tmp_path / "database_url").write_text("postgresql+asyncpg://file\n")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert database_url_from_env(tmp_path) == "postgresql+asyncpg://file"


def test_should_refuse_without_database_url_anywhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from club_server.db.url import database_url_from_env

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        _ = database_url_from_env(tmp_path)


def test_should_not_import_settings_anywhere_under_alembic():
    """Nothing under ``alembic/`` may import ``club_server.config``: that is
    the import that drags every required setting back in."""
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "alembic").rglob("*.py")
        if "club_server.config" in p.read_text()
    ]
    assert offenders == []


def test_should_refuse_data_migration_without_upload_dir(
    monkeypatch: pytest.MonkeyPatch,
):
    """The data migrations that move files read ``UPLOAD_DIR`` from the
    environment; unset, they must stop with a message naming it rather than
    work on some default directory."""
    from club_server.db.backfills import upload_dir_from_env

    monkeypatch.delenv("UPLOAD_DIR", raising=False)
    with pytest.raises(RuntimeError, match="UPLOAD_DIR"):
        _ = upload_dir_from_env()


def test_should_read_upload_dir_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from club_server.db.backfills import upload_dir_from_env

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    assert upload_dir_from_env() == tmp_path
