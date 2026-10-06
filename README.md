# Club Server

FastAPI backend for club management. This README covers running the server on
a developer machine. Tests are documented in [`tests/README.md`](tests/README.md),
the contribution workflow in [`CONTRIBUTING.md`](CONTRIBUTING.md), and the API
in the running server's `/docs`.

## Prerequisites

- **PostgreSQL 16 or 17** binaries on the machine (`initdb`, `pg_ctl`,
  `psql`). The scripts run a native throwaway cluster; no service needs to be
  enabled.
  - Debian/Ubuntu: `sudo apt install postgresql` (binaries under
    `/usr/lib/postgresql/<version>/bin`, found automatically)
  - macOS: `brew install postgresql@16`
- **[uv](https://github.com/astral-sh/uv)** for Python dependencies.
- **[just](https://github.com/casey/just)** for the recipes below.

## Start the server

The one-command developer stack (cluster, migrations, sudo user, uvicorn,
and the isolated test stacks the SDK, CLI and app suites use) lives in
`native_deploy` (a separate tool).
This repo carries only what its own tests need: `start_db.sh`, a native
throwaway PostgreSQL cluster.

To run the server from here by hand: start a cluster, export the
configuration below, run the four commands.

```bash
./start_db.sh --db-port 5437 --data-dir ~/.local/share/club_server/dev
# export the Configuration section, then:
uv sync --frozen
uv run --frozen alembic upgrade head
uv run --frozen club_bootstrap devboot          # creates the sudo super-admin
uv run --frozen uvicorn club_server.main:app --host 0.0.0.0 --port 8101
```

| | |
|---|---|
| API base | `http://localhost:8101/v1` |
| Health | `http://localhost:8101/health` |
| OpenAPI / Swagger | `http://localhost:8101/docs` |
| Super-admin login | `sudo` / `devboot` |
| Cluster | `./start_db.sh --stop --data-dir ~/.local/share/club_server/dev` stops it |

`--frozen` matters: without it `uv` re-resolves and rewrites `uv.lock` as a side
effect (#310). Only `uv add` / `uv lock` should change the lockfile.

Other recipes:

```bash
just migrate                                            # alembic upgrade head
just migrate revision --autogenerate -m "description"   # new migration
just lint · just format · just typecheck · just check   # ruff, basedpyright, then tests
```

## Configuration

All configuration is **environment variables**, read by `club_server/config.py`
and `club_server/mailer/config.py` at startup. There is no config file.
The values in the table are the development set; export them before the
commands above (`native_deploy` does this for you).

Required (no in-code default; the server refuses to start without them):

| Variable | Development value |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://myclub:devpass@localhost:5437/myclub` |
| `SECRET_KEY` | any string |
| `UPLOAD_DIR`, `STATIC_DIR` | two directories under the data directory |
| `CORS_ALLOWED_ORIGINS` | `*` |
| `CLUB_NAME`, `CLUB_SHORT_NAME` | `Local Dev Club`, `LDC` |
| `LOGIN_URL`, `SERVER_BASE_URL` | `http://localhost:<port>` |
| `EMAIL_FROM_TRANSACTIONAL`, `EMAIL_FROM_BROADCAST` | `*@localhost.invalid` |
| `EMAIL_THEME_COLOR` | `#E8772E` |

Club identity deliberately has no default in code: an instance that omits it
fails at startup rather than inheriting another club's name (#311).

Optional, with defaults in code: `ENVIRONMENT` (`development`),
`ACCESS_TOKEN_EXPIRE_MINUTES`, `MAX_IMAGE_UPLOAD_SIZE_MB` / `MAX_PDF_…` /
`MAX_VIDEO_…`, `MEDIA_CONVERT_SCRIPT`,
`SCHEDULING_HORIZON_WEEKS`, the three module switches `CREDIT_SYSTEM_ENABLED`,
`EVALUATIONS_ENABLED` and `EVENT_MARKETING_ENABLED` (all off; their routes
answer 503 while off), `IDENTITY_VERIFICATION_REQUIRED` (on; off, a new user
registers straight to `pending` with no identity-document step),
`DEFAULT_COUNTRY_CODE` (unset; the club's country calling code, one to three
digits without `+`, e.g. `91` — reported by `GET /v1/capabilities` as
`defaultCountryCode` so the apps can complete a phone number typed without a
code; any other value stops the server at startup), `ENCRYPTION_KEY` (base64 of 32 bytes; encrypted uploads
answer 503 without it), `TRANSACTIONAL_EMAIL_PROVIDER`
and `BROADCAST_EMAIL_PROVIDER` (`console`, i.e. print instead of send; `resend`
/ `brevo` need `RESEND_API_KEY` / `BREVO_API_KEY`), `EMAIL_LOGO_PATH`,
`EMAIL_AFFILIATION`. The full list with types is the two
`Settings` classes.

Dev credentials: `POSTGRES_PASSWORD` (`devpass`; the local cluster is
`--auth=trust`, so the value only appears in the URL), `BOOTSTRAP_PASSWORD`
(`devboot`), `SECRET_KEY`.

## Without `just`

`start_db.sh` works on its own; the commands under "Start the server" are
already the raw form:

```bash
./start_db.sh --db-port 5437 --data-dir ~/.local/share/club_server/dev   # cluster only
./start_db.sh --stop --data-dir ~/.local/share/club_server/dev
```

Alembic needs only `DATABASE_URL`, from the environment or, as in a container
deployment, from `/run/secrets/database_url`; the data migrations that move
files also read `UPLOAD_DIR` (#420). With the dev cluster running:

```bash
DATABASE_URL=postgresql+asyncpg://myclub:devpass@localhost:5437/myclub \
UPLOAD_DIR=~/.local/share/club_server/dev/uploads \
uv run --frozen alembic revision --autogenerate -m "description"
```

## Troubleshooting

**Port in use.** Find what holds the server port with
`lsof -nP -iTCP:8101 -sTCP:LISTEN`, or start uvicorn on another `--port`.

**PostgreSQL binaries not found.** `start_db.sh` looks in `PATH`, then the
Homebrew `postgresql@16` locations, then `/usr/lib/postgresql/<version>/bin`.
Put the directory holding `initdb` on `PATH` if yours is elsewhere.

**Stale cluster.** `./start_db.sh --reset ...` drops all tables; deleting
`~/.local/share/club_server/dev` starts from nothing.

## Contributing

All changes go through GitHub issues and pull requests against `main`. Read
[`CONTRIBUTING.md`](CONTRIBUTING.md) first.
