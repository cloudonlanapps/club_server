"""Deciding an application's layout and drawing its forms (#93, R14–R20).

``decide_layout`` runs on save: it refuses the template when a text lacks a
print language, measures it for the worst-case member and returns the
layout to store, with a frozen copy of the profile options it was measured
with (#96, #97). ``draw`` produces one member's PDF (or the preview's). A
saved or published template is drawn only from its stored layout and its
frozen options and is never measured again, so a download never fails; a
draft's preview is measured on the spot. The texts are the template's own
custom text; nothing is read from the clause library (#107).
"""

from datetime import date

from ..exceptions import ApplicationDoesNotFitException, ApplicationIncompleteException
from .application_catalogue import JsonDict, labels
from .application_pdf_builder import ApplicationFormBuilder
from .application_pdf_fit import check, choose_layout
from .application_pdf_layout import BOTTOM, LAYOUTS, layout_named
from .application_text import incomplete, lacking, printable

# The stored layout's copy of the profile options it was measured with.
FROZEN_OPTIONS = "profile_options"


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


def decide_layout(template: JsonDict, options: JsonDict, today: date) -> JsonDict:
    """The layout to store on save, with the options it was measured with
    (R13, R14, R16, R17)."""
    document = printable(template)
    missing = missing_texts(document)
    if missing:
        raise ApplicationIncompleteException(missing)
    decided = check(document, labels(), options, today)
    decided[FROZEN_OPTIONS] = options
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

    With a stored layout the form is drawn once, as stored; without one (a
    draft's preview) it is measured for this member first (R19).
    ``options`` are those ``frozen_options`` gives.
    """
    document = printable(template)
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
