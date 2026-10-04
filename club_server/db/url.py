"""The database URL for alembic, found where ``Settings`` would find it.

alembic must not construct ``Settings`` (#420), but it must still find the URL
in the same places the server does: the environment first, then the secrets
directory, which is how a container deployment delivers it — as a file, so it
stays out of ``docker inspect``. Reading only the environment broke every
container deploy at ``alembic upgrade head``.

Same order as ``club_server.config``: the environment wins where both exist.
"""

import os
from pathlib import Path

SECRETS_DIR = Path("/run/secrets")


def database_url_from_env(secrets_dir: Path = SECRETS_DIR) -> str:
    """``DATABASE_URL`` from the environment, else from
    ``<secrets_dir>/database_url``; refuse when neither holds a value."""
    value = os.environ.get("DATABASE_URL")
    if value:
        return value
    secret = secrets_dir / "database_url"
    if secret.is_file():
        value = secret.read_text().strip()
        if value:
            return value
    raise RuntimeError(
        f"DATABASE_URL must be set to run alembic, in the environment or in {secret}"
    )
