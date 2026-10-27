"""The club's languages in application texts (#133, applications R28–R30).

The club's languages are the profile options' languages. Every text an
admin reads (templates, library texts, labels) shows only those; one stored
in a language the club has since removed is kept and hidden, and a write
that sends a text without it keeps it, so adding the language back
restores it. A text with none of the club's languages is shown as stored.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..exceptions import ApplicationLanguageNotOfferedException
from ..schemas.language import is_language_code
from .profile_options import ProfileOptionsService


async def club_languages(db: AsyncSession) -> list[str]:
    """The club's language codes, as the profile options list them."""
    return await ProfileOptionsService(db).languages()


def check_offered(languages: list[str], club: list[str]) -> None:
    """Refuse print languages the club does not have (R29)."""
    absent = [lang for lang in languages if lang not in club]
    if absent:
        raise ApplicationLanguageNotOfferedException(absent)


def check_written(value: Any, club: list[str]) -> None:
    """Refuse a text written in a language the club does not have (R30)."""
    absent = sorted(_languages_of(value) - set(club))
    if absent:
        raise ApplicationLanguageNotOfferedException(absent)


def _languages_of(value: Any) -> set[str]:
    if is_text(value):
        entries: dict[str, str] = value
        return set(entries)
    if isinstance(value, list):
        items: list[Any] = value  # pyright: ignore[reportUnknownVariableType]
        found: set[str] = set()
        for item in items:
            found |= _languages_of(item)
        return found
    if isinstance(value, dict):
        fields: dict[str, Any] = value  # pyright: ignore[reportUnknownVariableType]
        found = set[str]()
        for v in fields.values():
            found |= _languages_of(v)
        return found
    return set()


def is_text(value: Any) -> bool:
    """Whether ``value`` is one text, ``{language: text}``."""
    if not isinstance(value, dict) or not value:
        return False
    entries: dict[Any, Any] = value  # pyright: ignore[reportUnknownVariableType]
    return all(is_language_code(k) and isinstance(v, str) for k, v in entries.items())


def shown(value: Any, club: list[str]) -> Any:
    """``value`` with every text in the club's languages only."""
    if is_text(value):
        entries: dict[str, str] = value
        kept = {k: v for k, v in entries.items() if k in club}
        return kept or entries
    if isinstance(value, list):
        items: list[Any] = value  # pyright: ignore[reportUnknownVariableType]
        return [shown(item, club) for item in items]
    if isinstance(value, dict):
        fields: dict[str, Any] = value  # pyright: ignore[reportUnknownVariableType]
        return {k: shown(v, club) for k, v in fields.items()}
    return value


def kept(sent: Any, stored: Any, club: list[str]) -> Any:
    """``sent`` with the stored texts' hidden languages kept, matched by
    place: the same key, or the same index in a list."""
    if is_text(sent) and is_text(stored):
        old: dict[str, str] = stored
        hidden = {k: v for k, v in old.items() if k not in club}
        return {**hidden, **sent}
    if isinstance(sent, list) and isinstance(stored, list):
        new: list[Any] = sent  # pyright: ignore[reportUnknownVariableType]
        before: list[Any] = stored  # pyright: ignore[reportUnknownVariableType]
        return [
            kept(item, before[i], club) if i < len(before) else item
            for i, item in enumerate(new)
        ]
    if isinstance(sent, dict) and isinstance(stored, dict):
        fields: dict[str, Any] = sent  # pyright: ignore[reportUnknownVariableType]
        previous: dict[str, Any] = stored  # pyright: ignore[reportUnknownVariableType]
        return {
            k: kept(v, previous[k], club) if k in previous else v
            for k, v in fields.items()
        }
    return sent  # pyright: ignore[reportUnknownVariableType]
