"""An event's or a group's gender criterion: a list of genders (#25, #26).

Eligibility R31–R35. The list is stored as JSON, ``NULL`` for none; an empty
list is no criterion, and a member passes a criterion when their own gender
is in it. The single ``gender`` that came before is kept on the API for a
release: a list of one on writes, the one value on reads when the list has
exactly one.
"""

import json
from collections.abc import Sequence
from enum import Enum
from typing import Protocol


class _GenderWrite(Protocol):
    gender: Enum | None
    genders: list[Enum] | None

    @property
    def model_fields_set(self) -> set[str]: ...


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


def single_gender(genders: list[str]) -> str | None:
    """R34: the old single ``gender``, when the list holds exactly one."""
    return genders[0] if len(genders) == 1 else None


def resolve_gender_write[M: _GenderWrite](model: M) -> M:
    """R33, R34: a write's gender criterion, as ``genders``.

    The old ``gender`` becomes a list of one (``null`` clears), and
    ``genders`` then counts as sent. Sending both, or repeating a value, is
    refused.
    """
    sent = model.model_fields_set
    if "gender" in sent and "genders" in sent:
        raise ValueError("Send genders or gender, not both")
    if "gender" in sent:
        model.genders = [model.gender] if model.gender is not None else None
        sent.add("genders")
    values = _values(model.genders)
    if len(set(values)) != len(values):
        raise ValueError("genders must not repeat a value")
    return model
