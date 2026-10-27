"""profile option language codes

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-10-28 12:00:00.000000

Rewrites the stored ``profile_options`` preference so each language is
``{code, name}`` (#133): a known name takes its code (English → en,
Marathi → mr, Hindi → hi, …), any other ``x-<name>``, so every stored
language is kept; English is added first when missing. The data step is
``club_server.db.backfills.profile_languages`` so it is tested against the
models. Members' preferred languages stay the names they chose. The
downgrade writes the names back.
"""

from typing import Any, Sequence, Union
import json

from alembic import op
from sqlalchemy import text

from club_server.db.backfills.profile_languages import migrate_profile_languages


# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Give each stored profile-options language a code."""
    _ = migrate_profile_languages(op.get_bind())


def downgrade() -> None:
    """Store the languages as names again."""
    conn = op.get_bind()
    row = conn.execute(
        text("SELECT value FROM system_preferences WHERE key = 'profile_options'")
    ).first()
    if row is None:
        return
    value: dict[str, Any] = dict(row[0])
    value["languages"] = [
        e["name"] if isinstance(e, dict) else e for e in value.get("languages") or []
    ]
    _ = conn.execute(
        text(
            "UPDATE system_preferences SET value = CAST(:value AS jsonb) "
            "WHERE key = 'profile_options'"
        ),
        {"value": json.dumps(value, ensure_ascii=False)},
    )
