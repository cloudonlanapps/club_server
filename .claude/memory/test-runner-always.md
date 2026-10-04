---
name: test-runner-always
description: "No agents" / "no parallel" means no parallel implementation agents; test runs still always go through the test-runner agent.
metadata:
  type: feedback
---

When the user says "no parallel thread or agent", they mean implementation:
don't fan work out across worktrees to several agents at once. It never
excludes the `test-runner` agent — that is part of the framework and every
pytest run goes through it (tests/CLAUDE.md), run sequentially if asked.

**Why:** on 2026-09-23 I read "no agent" as "run pytest myself" and ran
`just tests::auto` directly ~18 times; each left its postgres cluster
running (ports 5440–5457) because only the agent's exit trap tears it
down. The user corrected: "test-runner is always available, and should be
used. its part of the framework."

**How to apply:** sequential implementation in one session, one worktree at a
time when told; every test run (subset or full) via `test-runner`. If a
direct run ever happens, follow it with `just tests::cleanup` after
checking no other run is live. Related: [[parallel-worktree-test-runs]].
