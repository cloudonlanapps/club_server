---
name: scope-test-runs
description: Run only the test files a change touches; keep the full or module-wide suite for before a PR
metadata:
  type: feedback
---

For a small change, run only the test files that exercise the changed code, plus the requirement-coverage test. Don't re-run a whole module's suite. Keep the full suite for before a PR, or for changes to shared helpers and dependencies.

**Why:** On 2026-10-01 I added `inUse` to the template schema plus one evidence endpoint in club_server, then ran all 22 evaluation test files: 304 tests, about 10 minutes. The user asked: "why do you need to run full run for a small change of adding just one parameter in schema". The templates, template-items, item-copies and media files would have covered it in about 4 minutes.

**How to apply:** before delegating a test run, list the call sites of what changed, map them to their test files, and pass only those. Related: [[server-tdd-policy]].
