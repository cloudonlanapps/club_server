"""Strip a retired role from every stored roles list (#400, #514).

``Role.member`` gated nothing and was never granted automatically; it has
been dropped from the enum (#400). ``super_admin`` followed (#514): the
super admin is the ``is_super_admin`` flag, and the value stored as a role
granted nothing. Any row still naming a retired role would fail to parse
into a role the code knows, so the migrations remove the string from the
``users.roles`` JSON (``{"roles": [...]}``) wherever it appears and leave
every other role in place, in order.

Idempotent: a second run finds no such row and changes nothing.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection

RETIRED_ROLE = "member"


def strip_role(conn: Connection, role: str) -> int:
    """Remove ``role`` from every user's roles list; return rows changed."""
    result = conn.execute(
        text(
            """
            UPDATE users
            SET roles = jsonb_set(
                roles::jsonb,
                '{roles}',
                (roles::jsonb -> 'roles') - :role
            )::text
            WHERE (roles::jsonb -> 'roles') ? :role
            """
        ),
        {"role": role},
    )
    return result.rowcount


def strip_member_role(conn: Connection) -> int:
    """Remove ``member`` from every user's roles list; return rows changed."""
    return strip_role(conn, RETIRED_ROLE)
