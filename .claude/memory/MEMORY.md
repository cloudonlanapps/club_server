## How to work

- [explain-one-by-one-plainly](explain-one-by-one-plainly.md) — When reviewing a list of findings with the user, take one point at a time in plain terms, define terms, and weigh how often a case really happens
- [answer-point-by-point](answer-point-by-point.md) — Answer each question separately and in order; never bury a contradiction in prose
- [reply-format-numbered-items](reply-format-numbered-items.md) — User wants brief replies as numbered, tagged items ([Info]/[DECIDE]), max 2 sentences or 30 words each, grouped when more than 5
- [worktrees-and-stacked-prs](worktrees-and-stacked-prs.md) — Do code work in git worktrees, never the main checkout; split multi-part work into stacked PRs rather than parallel ones off main
- [corner-cases-as-failing-tests](corner-cases-as-failing-tests.md) — Don't over-engineer server fixes for rare corner cases; prefer a UI guard, and record a thought-up scenario as a failing test rather than prose.
- [scope-test-runs](scope-test-runs.md) — Run only the test files a change touches; keep the full or module-wide suite for before a PR

## club_server

- [existing-tests-are-a-ledger](existing-tests-are-a-ledger.md) — On this repo, existing tests are not to be modified to make new code pass; when a spec change obsoletes one, the change is listed in the PR body with the rule that obsoleted it.
- [generate-alembic-migrations](generate-alembic-migrations.md) — Always create club_server migrations with `alembic revision`, never by hand-writing the file
- [programme-oneoff-umbrella](programme-oneoff-umbrella.md) — Programme and one-off requirements are finished (#363, closed); implementation is #384, twenty-four children in six dependency-ordered phases.
- [server-is-generic-fit-before-extending](server-is-generic-fit-before-extending.md) — club_server stays club-neutral, but trivial schema changes (a JSON column, a field) are cheap and preferred over contorting data into existing fields; design output → model → server → client
- [server-tdd-policy](server-tdd-policy.md) — club_server changes follow docs/conventions/tdd_rules.md — requirements doc first, red tests with requirement markers, then code
- [test-runner-always](test-runner-always.md) — No agents" / "no parallel" means no parallel implementation agents; test runs still always go through the test-runner agent.
- [test-session-rollback-on-4xx](test-session-rollback-on-4xx.md) — In club_server tests, any 4xx response rolls back the shared session and discards users the helpers only flushed; commit before a request expected to fail.
