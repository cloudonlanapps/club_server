"""Data-backfill helpers invoked from Alembic data migrations.

Each module here exposes a pure function that takes a SQLAlchemy
``Connection`` (plus any context like ``upload_dir``) and performs the
backfill. The matching Alembic revision is a thin wrapper so the same
logic can be unit-tested against the per-function test session without
spinning up Alembic.
"""

import os
from pathlib import Path


def upload_dir_from_env() -> Path:
    """The uploads directory a data migration works on, from ``UPLOAD_DIR``.

    Read from the environment rather than ``Settings`` so that alembic needs
    only ``DATABASE_URL`` plus this, and refuse to guess: a migration that
    moved files under some default directory would silently do nothing to
    the real one (#420).
    """
    value = os.environ.get("UPLOAD_DIR")
    if not value:
        raise RuntimeError(
            "UPLOAD_DIR must be set to run this data migration; "
            "it names the directory whose files the migration moves"
        )
    return Path(value)
