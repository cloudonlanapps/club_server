---
name: generate-alembic-migrations
description: "Always create club_server migrations with `alembic revision`, never by hand-writing the file"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5d58fe48-36e2-4f1d-b2e0-4aabf377a2a8
  modified: 2026-09-01T11:10:05.517Z
---

Create migrations in `server/club_server` with `uv run alembic revision -m "..."`
(add `--autogenerate` when the models already describe the change). Never
hand-write the file with an invented revision id and a guessed `down_revision`.

**Why:** hand-writing both fields got it wrong twice in one migration, and the
mistake only surfaced at deploy time, on a live server, with both dev stacks
already stopped. The revision id `a1b2c3d4e5f6` was already taken by
`add_middle_name_to_users.py`, which alembic reports as a *cycle* rather than a
duplicate, so the error does not name the real problem. The `down_revision` was
set to `z0a1b2c3d4e5` because that file sorted last in `ls alembic/versions` —
but alphabetical order is not revision order, and the true head was
`c8d9e0f1a2b3`. `alembic revision` reads the graph and fills in both correctly.

This repo makes the trap easy to fall into: it mixes generated ids
(`fd2135bfaf16`) with a hand-rolled alphabetical sequence
(`x8y9z0a1b2c3`, `y9z0a1b2c3d4`, `z0a1b2c3d4e5`), so the next id in that
sequence *looks* like it should be `a1b2c3d4e5f6` — which is precisely the one
already in use.

**How to apply:** run `uv run alembic revision -m "..."`, then edit the
generated `upgrade()`/`downgrade()` bodies. Before committing, confirm
`uv run alembic heads` prints exactly one head and that it is the new revision.
The test suite will not catch a broken migration — `conftest.py` builds tables
with `Base.metadata.create_all`, so migrations are never exercised by tests and
a full green suite says nothing about whether the server can start.
See [[club-server-migrations-untested]].
