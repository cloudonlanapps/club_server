"""Give each profile-options language a code (#133, applications R28).

Before #133 ``profile_options.languages`` was a list of names
(``["English", "Marathi"]``). Each becomes ``{code, name}``: a known name
takes its code, any other ``x-<name>``, so every stored language is kept;
English is put first when missing, since a club always has it. Positions
are left as they are.

Idempotent: an entry that already has a code is kept as it is.
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ...schemas.language import ENGLISH, ENGLISH_NAME, code_for_name

PROFILE_OPTIONS_KEY = "profile_options"


def coded_language(entry: Any) -> Any:
    """One language as ``{code, name}``; anything else as it is."""
    if isinstance(entry, str):
        return {"code": code_for_name(entry), "name": entry}
    return entry


def _code_of(entry: Any) -> Any:
    if not isinstance(entry, dict):
        return None
    fields: dict[str, Any] = entry  # pyright: ignore[reportUnknownVariableType]
    return fields.get("code")


def coded_profile_options(value: Any) -> Any:
    """The profile options with every language coded and English in."""
    if not isinstance(value, dict):
        return value
    options: dict[str, Any] = dict(value)  # pyright: ignore[reportUnknownArgumentType]
    raw: Any = options.get("languages") or []
    if not isinstance(raw, list):
        return options
    entries: list[Any] = raw  # pyright: ignore[reportUnknownVariableType]
    languages: list[Any] = [coded_language(entry) for entry in entries]
    if not any(_code_of(e) == ENGLISH for e in languages):
        languages.insert(0, {"code": ENGLISH, "name": ENGLISH_NAME})
    options["languages"] = languages
    return options


def migrate_profile_languages(conn: Connection) -> int:
    """Rewrite the stored profile options with coded languages; return the
    rows changed (0 or 1)."""
    row = conn.execute(
        text("SELECT value FROM system_preferences WHERE key = :key"),
        {"key": PROFILE_OPTIONS_KEY},
    ).first()
    if row is None:
        return 0
    stored: Any = row[0]
    coded = coded_profile_options(stored)
    if coded == stored:
        return 0
    _ = conn.execute(
        text(
            "UPDATE system_preferences SET value = CAST(:value AS jsonb) "
            "WHERE key = :key"
        ),
        {"key": PROFILE_OPTIONS_KEY, "value": json.dumps(coded, ensure_ascii=False)},
    )
    return 1
