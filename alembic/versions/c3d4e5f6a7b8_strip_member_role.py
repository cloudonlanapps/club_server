"""strip the retired member role from users.roles (#400)

Revision ID: c3d4e5f6a7b8
Revises: a1e2v3a4l5u6
Create Date: 2026-09-04 17:10:00.000000

``Role.member`` is removed from the enum; this strips the string from every
stored roles list so no row names a role the code no longer knows. The body
lives in ``club_server.db.backfills.member_role`` so it is unit-tested
against the per-function test DB without spinning up Alembic.

``downgrade()`` is a no-op: nothing ever depended on the role, so there is
nothing to put back.
"""

from typing import Sequence, Union

from alembic import op

from club_server.db.backfills.member_role import strip_member_role


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "a1e2v3a4l5u6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    _ = strip_member_role(op.get_bind())


def downgrade() -> None:
    pass
