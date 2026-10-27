"""Deciding an application's layout and drawing its forms (#93, R14–R20).

``decide_layout`` runs on save: it refuses the template when a text lacks a
print language or a gap names no fact, measures it with its gaps filled for
the worst-case member and returns the layout to store, with frozen copies of
the profile options and fact formats it was measured with (#96, #97, #126). ``draw`` produces one member's PDF (or the preview's). A
saved or published template is drawn only from its stored layout and its
frozen options and is never measured again, so a download never fails; a
draft's preview is measured on the spot. The texts are the template's own
custom text, their gaps filled from its facts (#107, #126).
"""

from datetime import date

from ..exceptions import (
    ApplicationDoesNotFitException,
    ApplicationIncompleteException,
    ApplicationUnknownFactException,
)
from .application_catalogue import JsonDict, labels, library
from .application_pdf_builder import ApplicationFormBuilder
from .application_pdf_fit import check, choose_layout
from .application_pdf_layout import BOTTOM, LAYOUTS, layout_named
from .application_text import filled, incomplete, lacking, printable, unknown_gaps

# The stored layout's copy of the profile options it was measured with.
FROZEN_OPTIONS = "profile_options"
# ... and of the library's formats and choices its facts were printed with.
FROZEN_FORMATS = "fact_formats"


def missing_texts(template: JsonDict) -> list[JsonDict]:
    """Texts, and label overrides, missing print languages (R9)."""
    langs: list[str] = template["languages"]
    found = incomplete(template)
    overrides: JsonDict = template.get("label_overrides") or {}
    for key, value in overrides.items():
        entry = lacking(f"labelOverrides.{key}", value, langs)
        if entry:
            found.append(entry)
    return found


def fact_formats() -> JsonDict:
    """What a fact is printed with: the library's ``formats`` and ``choices``."""
    lib = library()
    return {"formats": lib["formats"], "choices": lib["choices"]}


def decide_layout(template: JsonDict, options: JsonDict, today: date) -> JsonDict:
    """The layout to store on save, measured on the filled texts, with the
    options and fact formats it was measured with (R9b, R13, R14, R16, R17)."""
    document = printable(template)
    missing = missing_texts(document)
    if missing:
        raise ApplicationIncompleteException(missing)
    unknown = unknown_gaps(template)
    if unknown:
        raise ApplicationUnknownFactException(unknown)
    formats = fact_formats()
    decided = check(filled(document, formats), labels(), options, today)
    decided[FROZEN_OPTIONS] = options
    decided[FROZEN_FORMATS] = formats
    return decided


def frozen_options(stored: JsonDict | None, live: JsonDict) -> JsonDict:
    """The profile options a form is drawn with: the stored layout's frozen
    copy, or ``live`` for a draft (R14)."""
    if stored is None:
        return live
    frozen: JsonDict = stored.get(FROZEN_OPTIONS, live)
    return frozen


def draw(
    template: JsonDict,
    member: JsonDict,
    options: JsonDict,
    logo: bytes | None,
    today: date,
    stored: JsonDict | None,
    preview: bool = False,
    publish_id: str | None = None,
) -> bytes:
    """One form as PDF bytes.

    With a stored layout the form is drawn once, as stored, its facts
    printed with the formats frozen on save; without one (a draft's preview)
    it is measured for this member first (R14, R15a, R19).
    ``options`` are those ``frozen_options`` gives.
    """
    formats: JsonDict = (stored or {}).get(FROZEN_FORMATS) or fact_formats()
    document = filled(printable(template), formats)
    all_labels = labels()
    if stored:
        layout = layout_named(stored["name"])
        if layout is None:
            raise ValueError(f"unknown stored layout {stored['name']!r}")
        probe = ApplicationFormBuilder(
            document, member, all_labels, options, logo, today, layout
        )
        spread: JsonDict = stored["spread"]
        extra: float = spread[
            "with_guardian" if probe.shows_guardian() else "without_guardian"
        ]
    else:
        chosen = choose_layout(document, member, all_labels, options, logo, today)
        if chosen is None:
            b = ApplicationFormBuilder(
                document,
                member,
                all_labels,
                options,
                logo,
                today,
                LAYOUTS[-1],
                measure=True,
            )
            _ = b.build()
            raise ApplicationDoesNotFitException((b.page1_end or 0) - BOTTOM)
        layout, extra, _ = chosen
    pdf = ApplicationFormBuilder(
        document,
        member,
        all_labels,
        options,
        logo,
        today,
        layout,
        extra,
        preview=preview,
        publish_id=publish_id,
    ).build()
    return bytes(pdf.output())
