---
name: corner-cases-as-failing-tests
description: "Don't over-engineer server fixes for rare corner cases; prefer a UI guard, and record a thought-up scenario as a failing test rather than prose."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 30f36393-65e5-4839-9547-8ac36c2c55ee
  modified: 2026-09-26T12:14:24.351Z
---

On 2026-09-26, after #479 (refund into a closed credit account → reopen it,
audit it, amend R56), the user said sessions "take over thinking" and try to
address every corner case. Often it is enough to **disable the action in the
UI** when it is not appropriate, rather than complicating the server.

And: when a corner-case scenario is thought of, **add a test and let it
fail** (strict xfail keyed to an issue, per #385) instead of writing long
prose in issues or board tasks. A failing test explains the case better than
a paragraph that is hard to grasp.

**Why:** rare scenarios were turning into server rules, migrations and
requirement rewrites whose cost outweighed the risk; long review write-ups
were hard to digest.

**How to apply:** when a finding is a rare admin-driven corner case, propose
"UI guard + failing test" first and let the user choose server logic only
if needed. Record new scenarios as failing tests, not paragraphs. Don't go
back and revert already-merged corner-case work (user said so explicitly).
Related: [[existing-tests-are-a-ledger]], [[reply-format-numbered-items]].
