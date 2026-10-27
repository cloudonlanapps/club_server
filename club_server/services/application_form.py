"""Deciding an application's layout and drawing its forms (#93, R14–R20).

``decide_layout`` runs on save: it resolves the template, refuses it when a
text lacks a print language or a reference is broken, measures it for the
worst-case member and returns the layout to store. ``draw`` produces one
member's PDF (or the preview's) with the stored layout, measuring again
first when the catalogue, the clause library or the fonts have changed
since it was stored.
"""

from datetime import date

from ..exceptions import ApplicationDoesNotFitException, ApplicationIncompleteException
from .application_catalogue import JsonDict, fingerprint, labels, library
from .application_pdf_builder import ApplicationFormBuilder
from .application_pdf_fit import check, choose_layout
from .application_pdf_layout import BOTTOM, LAYOUTS, layout_named
from .application_text import incomplete, lacking, resolve

FINGERPRINT = "fingerprint"


def resolved(template: JsonDict) -> JsonDict:
    """The template with its references resolved; raises on a broken one."""
    return resolve(template, library())


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
    """The layout to store on save (R13, R16, R17)."""
    document = resolved(template)
    missing = missing_texts(document)
    if missing:
        raise ApplicationIncompleteException(missing)
    decided = check(document, labels(), options, today)
    decided[FINGERPRINT] = fingerprint()
    return decided


def is_current(stored: JsonDict | None) -> bool:
    """Whether a stored layout was measured with today's catalogue and fonts."""
    return stored is not None and stored.get(FINGERPRINT) == fingerprint()


def draw(
    template: JsonDict,
    member: JsonDict,
    options: JsonDict,
    logo: bytes | None,
    today: date,
    stored: JsonDict | None,
    preview: bool = False,
) -> bytes:
    """One form as PDF bytes.

    With a stored layout the form is drawn once; without one (a draft's
    preview) it is measured for this member first (R19).
    """
    document = resolved(template)
    all_labels = labels()
    layout = layout_named(stored["name"]) if stored else None
    if stored and layout:
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
    ).build()
    return bytes(pdf.output())
