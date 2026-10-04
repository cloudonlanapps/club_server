"""strip the super_admin value from users.roles (#514)

Revision ID: n4c5d6e7f8a9
Revises: m3b4c5d6e7f8
Create Date: 2026-09-28 10:00:00.000000

``Role.super_admin`` is removed from the enum: the super admin is the
``is_super_admin`` flag, and the value stored as a role granted nothing.
This strips the string from every stored roles list, as #400 did for
``member``. The body lives in ``club_server.db.backfills.member_role``.

``downgrade()`` is a no-op: the value granted nothing, so there is
nothing to put back.
"""

from typing import Sequence, Union

from alembic import op

from club_server.db.backfills.member_role import strip_role


revision: str = "n4c5d6e7f8a9"
down_revision: Union[str, None] = "m3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RETIRED_ROLE = "super_admin"


def upgrade() -> None:
    _ = strip_role(op.get_bind(), RETIRED_ROLE)


def downgrade() -> None:
    pass
