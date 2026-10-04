# Developer entry points for club_server. Tests live in their own module:
#   just tests::auto [pytest args]   (see tests/README.md)
#
# Recipe parameters are passed to the shell as real positional arguments, so
# forwarded arguments keep their quoting (#352).
set positional-arguments

mod tests

# Dev cluster (port 5437) and its data dir; override either in the environment.
dev_db_port  := env_var_or_default('DB_PORT', '5437')
dev_data_dir := env_var_or_default('START_DEV_DATA_DIR', env_var_or_default('XDG_DATA_HOME', env_var('HOME') / '.local/share') / 'club_server/dev')

#   just migrate                                    # upgrade head
#   just migrate revision --autogenerate -m "msg"   # new migration
#   just migrate history
#
# Alembic reads DATABASE_URL; the data migrations that move files read
# UPLOAD_DIR (#420). Both are exported here.
#
# Alembic against the dev cluster (started if needed); no args = upgrade head
migrate *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    ./start_db.sh --db-port "{{ dev_db_port }}" --data-dir "{{ dev_data_dir }}"
    mkdir -p "{{ dev_data_dir }}/uploads"
    export DATABASE_URL="postgresql+asyncpg://myclub:${POSTGRES_PASSWORD:-devpass}@localhost:{{ dev_db_port }}/myclub"
    export UPLOAD_DIR="{{ dev_data_dir }}/uploads"
    if [ $# -eq 0 ]; then set -- upgrade head; fi
    uv run --frozen alembic "$@"

# Lint and format check
lint:
    uv run --frozen ruff check .
    uv run --frozen ruff format --check .

# Auto-fix formatting and lint issues
format:
    uv run --frozen ruff format .
    uv run --frozen ruff check --fix .

# Type check
typecheck:
    uv run --frozen basedpyright

# All checks: lint + typecheck + tests on an isolated DB
check: lint typecheck
    just tests::auto
