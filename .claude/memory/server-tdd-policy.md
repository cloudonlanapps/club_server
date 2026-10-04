---
name: server-tdd-policy
description: club_server changes follow docs/conventions/tdd_rules.md — requirements doc first, red tests with requirement markers, then code
metadata:
  type: feedback
---

When changing club_server, follow `docs/conventions/tdd_rules.md`.

The order is:
1. Update the requirements doc and mark new or changed rules `[GAP] (#issue)`.
2. Write failing tests first. Each test is named `test_should_<x>_when_<y>`, covers one rule, and carries `@pytest.mark.requirement("doc:Rn")`.
3. Stub new endpoints or services with `NotImplementedError`.
4. Implement.
5. Flip each `[GAP]` to `[✅]` or `[X]`.

Each mutation needs a follow-up query and assertion. Test every role that can reach an endpoint, both the allowed and the forbidden case. Check that a test fails when it should by breaking it by hand.

**Why:** on 2026-10-01, during the evaluations rework (club_server#535), the user reminded me: "when updating server, remember TDD policy".

**How to apply:** never write server code ahead of its red test. `tests/test_requirement_coverage.py` fails for every `[GAP]` rule that has no marked test, and that is the expected red state on a feature branch. See [[evaluations-design-surveyjs]].
