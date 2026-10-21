"""An event's or a group's gender criterion: a list of genders (#25, #26).

Eligibility R31–R35. The list is stored as JSON, ``NULL`` for none; an empty
list is no criterion, and a member passes a criterion when their own gender
is in it. The single ``gender`` that came before is no longer on the API
(R34, #54).
"""

import json
from collections.abc import Sequence
from enum import Enum
from typing import Protocol


class _GenderWrite(Protocol):
    genders: list[Enum] | None


def _values(genders: Sequence[Enum | str] | None) -> list[str]:
    return [str(getattr(g, "value", g)) for g in genders or []]


def encode_genders(genders: Sequence[Enum | str] | None) -> str | None:
    """A gender list as stored: JSON, or NULL for no criterion."""
    values = _values(genders)
    return json.dumps(values) if values else None


def decode_genders(raw: str | None) -> list[str]:
    """A stored gender list; empty for no criterion."""
    return [str(g) for g in json.loads(raw)] if raw else []


def admits_gender(genders: list[str], user_gender: str | None) -> bool:
    """R32: no criterion admits everyone; a criterion admits a listed gender."""
    return not genders or user_gender in genders


def reject_repeated_genders[M: _GenderWrite](model: M) -> M:
    """R33: a write's ``genders`` must not repeat a value."""
    values = _values(model.genders)
    if len(set(values)) != len(values):
        raise ValueError("genders must not repeat a value")
    return model
