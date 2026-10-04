---
name: existing-tests-are-a-ledger
description: "On this repo, existing tests are not to be modified to make new code pass; when a spec change obsoletes one, the change is listed in the PR body with the rule that obsoleted it."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: edab6fab-ef25-461a-bfb7-687976345ca6
  modified: 2026-09-03T15:02:27.168Z
---

The user's standing instruction for implementation work (given for #384): "Follow TDD strictly; add tests, don't modify any of the existing tests -- read first."

**Why:** the requirement docs drifted from the code once before because tests were written to match code; existing tests are the evidence the docs were checked against, and silently rewriting them hides regressions.

**How to apply:** write new tests first (strict xfail keyed to the issue, per #385). Never weaken an existing assertion to make new code pass. When a specification change genuinely retires a behaviour an existing test pins (a removed endpoint, a rule the doc now forbids), make the smallest setup or inverse-assertion change and record every touched test in the PR body under "Existing tests changed or removed, and why", with the rule id. Deleted tests whose behaviour survives in another shape should be ported (see `tests/test_conflict_gates_semantics.py`), not dropped. See [[parallel-worktree-test-runs]] for how to run the suite.
