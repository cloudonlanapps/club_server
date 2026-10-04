# tests/CLAUDE.md

Context for working on tests. How to run them is in [`README.md`](README.md)
in this directory; how to write them is in `docs/conventions/tdd_rules.md`. This file only
adds what an agent needs to know.

## Running tests: delegate to the `test-runner` agent

When you need pytest as part of a task, use the `Agent` tool with
`subagent_type: "test-runner"` and pass pytest arguments in the prompt
(e.g. "Run `tests/test_events.py -k test_create`"). Do not run
`just tests::auto` or `uv run pytest` from the main session.

Why: the agent runs `just tests::auto` under an exit trap that always tears
the cluster down, tees output to `/tmp/club-test-<ts>.log`, and reports the
summary line back in a fixed shape, so the main session never pays for the
pytest output. (A shared cluster once carried the `media_in_use` view between
runs and broke setup; every run now gets its own.) If its report
says cleanup was not confirmed, run `just tests::cleanup`.

Independent issues go in separate worktrees, one test-runner agent each.

## Infrastructure

- `conftest.py` builds an `httpx.AsyncClient` over `ASGITransport` and
  overrides `get_db` with a per-test session; tables are created and dropped
  per function. Port comes from `TEST_DB_PORT`; 5437 is refused.
- `helpers.py`: `create_admin_user` (super-admin), `create_regular_admin_user`,
  `create_coach_user`, `create_guardian_user`. Each returns a bearer token.
- `test_requirement_coverage.py` parses the requirement docs it lists and fails
  when an in-scope rule has no `@pytest.mark.requirement("doc:Rn")` marker, or a
  marker names a rule no document defines. Move markers with the rules (#385).
- Structural guards exist alongside behaviour tests: `test_event_service_split.py`
  enforces that `services/event_types.py` is the only place an event's type is
  compared.

## Ledger rule

Existing tests are a ledger. Never weaken or delete one to make a change pass;
when a spec change genuinely obsoletes a test, change it and list every such
test in the PR body.
