"""The label catalogue and clause library that ship with the server (#93).

``assets/applications/labels.json`` holds the form's fixed wording and
``clauses.json`` the sentences a template builds its header items,
declaration, rules, selections and notes from, each in English, Marathi and
Hindi. Keys starting with ``_`` are notes for reviewers: they are dropped
when the files are read and never printed or served.

The fingerprint changes whenever the label catalogue or a PDF font changes;
a stored layout carrying an older one is measured again (applications R14).
The clause library is not part of it: a template holds only custom text, so
no change to the library can change a form (#107).
"""

import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Any

from .application_pdf_layout import FONT_DIR, FONT_FILES

JsonDict = dict[str, Any]

ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "applications"
LABELS_FILE = ASSET_DIR / "labels.json"
CLAUSES_FILE = ASSET_DIR / "clauses.json"
NOTE_PREFIX = "_"


def strip_notes(value: Any) -> Any:
    """Drop the "_" keys (notes for reviewers) from a JSON value."""
    if isinstance(value, dict):
        items: JsonDict = dict(value)  # pyright: ignore[reportUnknownArgumentType]
        return {
            k: strip_notes(v) for k, v in items.items() if not k.startswith(NOTE_PREFIX)
        }
    if isinstance(value, list):
        return [strip_notes(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def _read(path: Path) -> JsonDict:
    stripped: JsonDict = strip_notes(json.loads(path.read_text(encoding="utf-8")))
    return stripped


@cache
def labels() -> JsonDict:
    """The label catalogue: label id → {language: text}."""
    return _read(LABELS_FILE)


@cache
def library() -> JsonDict:
    """The clause library: ``formats``, ``choices`` and ``clauses``."""
    return _read(CLAUSES_FILE)


def catalogue_languages() -> list[str]:
    """The languages every label is written in, English first."""
    first: JsonDict = next(iter(labels().values()))
    return list(first)


@cache
def fingerprint() -> str:
    """A hash of the label catalogue and the PDF fonts."""
    digest = hashlib.sha256()
    for path in (LABELS_FILE, *(FONT_DIR / file for _, _, file in FONT_FILES)):
        digest.update(path.read_bytes())
    return digest.hexdigest()
