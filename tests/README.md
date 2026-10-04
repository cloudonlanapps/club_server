# Running the tests

One suite, 128 files, all of the same kind: each test drives the FastAPI app
in-process over a real PostgreSQL. No HTTP port is bound; the only thing a
run needs is a PostgreSQL cluster to talk to. Every test creates and drops
all tables, so a run must never point at a database holding data you want.

Recipes are invoked from the repo root as `just tests::<recipe>`; they live
in `tests/justfile`.

## Running

```bash
just tests::auto                                   # whole suite
just tests::auto tests/test_events.py              # one file
just tests::auto tests/test_events.py -k create    # pytest args pass through
just tests::auto -x -s tests/test_auth.py
```

Every run gets its **own PostgreSQL cluster** on a free port in 5440–5499,
under `/tmp/club_server_test_run_<port>`. There is no shared test database:
two runs against one cluster collide, since each test drops every table, and
a cluster that survives between sessions carries state into the next run.
Isolated clusters make concurrent runs (several worktrees, an agent running
tests while you do) safe. Port allocation is serialised under a lock, so two
runs started at the same moment cannot pick the same port (#348).

`auto` prints `==> port=<PORT>  data-dir=<DIR>` first and leaves the cluster
running when it finishes, so a failed run can still be inspected. Tear it
down afterwards:

```bash
just tests::stop <PORT>     # stop that cluster and remove its data-dir
just tests::cleanup         # stop and remove every leftover test cluster
```

Clusters live under `/tmp`, so anything forgotten expires with it. Ports
**5437, 5438 and 5439** are never used: 5437 is the local dev server's
cluster, the other two are reserved.

## Without `just`

```bash
./start_db.sh --db-port 5450 --data-dir /tmp/club_server_test_run_5450
TEST_DB_PORT=5450 uv run --frozen pytest [args]
./start_db.sh --stop --data-dir /tmp/club_server_test_run_5450
```

`tests/conftest.py` connects as `myclub` / `devpass` to database `myclub` on
`localhost:$TEST_DB_PORT` and refuses to run against 5437.
The cluster is created `--auth=trust`, so the password is never checked.

## Writing tests

Conventions (red-first, role double-verification, naming, isolation) are in
[`docs/conventions/tdd_rules.md`](../docs/conventions/tdd_rules.md). Tests that evidence a rule in a
`docs/*_requirements.md` carry `@pytest.mark.requirement("doc:Rn")`;
`test_requirement_coverage.py` fails when an in-scope rule has no test.
Use the helpers in `helpers.py` for users and tokens rather than hand-rolling
them.
