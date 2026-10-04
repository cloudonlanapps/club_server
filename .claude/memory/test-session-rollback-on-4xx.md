---
name: test-session-rollback-on-4xx
description: "In club_server tests, any 4xx response rolls back the shared session and discards users the helpers only flushed; commit before a request expected to fail."
metadata: 
  node_type: memory
  type: project
  originSessionId: 7cde5940-3690-4c45-9cf6-252c2fb00411
  modified: 2026-09-04T13:06:21.488Z
---

`tests/conftest.py` overrides `get_db` with one session per test that commits after every request and **rolls back on any exception**, including the HTTPException behind a 4xx and pydantic's 422. Helpers like `create_admin_user` / `create_coach_user` only `flush()`, so a failing request wipes every user created since the last successful request, and the next call answers 401 `USER_NOT_FOUND` for a token that was fine a moment ago.

**Why:** cost two red-run cycles on 2026-09-04 (venue and member-role tests) before the cause was found.

**How to apply:** `await db_session.commit()` after the helper calls when a test's next request is meant to be refused, or make the refused request last. Related: [[existing-tests-are-a-ledger]].
