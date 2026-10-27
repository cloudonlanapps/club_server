"""Deciding an application's layout and drawing its forms (#93, R14–R20).

``decide_layout`` runs on save: it refuses the template when a text lacks a
print language or a gap names no fact, measures it with its gaps filled for
the worst-case member and returns the layout to store, with frozen copies of
the profile options and fact formats it was measured with (#96, #97, #126). ``draw`` produces one member's PDF (or the preview's). A
saved or published template is drawn only from its stored layout and its
frozen options and is never measured again, so a download never fails; a
draft's preview is measured on the spot. The texts are the template's own
custom text, their gaps filled from its facts (#107, #126).

``draw_blank`` draws a published template's form with no member in it, and
where each member value goes: stored on publish, a download fills it
(``application_pdf_fill``, #128).
"""

from datetime import date

from ..exceptions import (
    ApplicationDoesNotFitException,
    ApplicationIncompleteException,
    ApplicationUnknownFactException,
)
from .application_catalogue import JsonDict, labels, library
from .application_pdf_builder import ApplicationFormBuilder
from .application_pdf_fit import ONE_SPREAD, check, choose_layout, spread_of
from .application_pdf_layout import BOTTOM, LAYOUTS, Layout, layout_named
from .application_text import filled, incomplete, lacking, printable, unknown_gaps

# The stored layout's copy of the profile options it was measured with.
FROZEN_OPTIONS = "profile_options"
# ... and of the library's formats and choices its facts were printed with.
FROZEN_FORMATS = "fact_formats"
# ... and of the club's labels it was measured with (#133).
FROZEN_LABELS = "labels"


def missing_texts(
    template: JsonDict, all_labels: JsonDict | None = None
) -> list[JsonDict]:
    """Texts, label overrides and the club's labels a template does not
    override, missing print languages (R9, #133)."""
    langs: list[str] = template["languages"]
    found = incomplete(template)
    overrides: JsonDict = template.get("label_overrides") or {}
    for key, value in overrides.items():
        entry = lacking(f"labelOverrides.{key}", value, langs)
        if entry:
            found.append(entry)
    for key, value in (all_labels or {}).items():
        entry = None if key in overrides else lacking(f"labels.{key}", value, langs)
        if entry:
            found.append(entry)
    return found


def fact_formats() -> JsonDict:
    """What a fact is printed with: the library's ``formats`` and ``choices``."""
    lib = library()
    return {"formats": lib["formats"], "choices": lib["choices"]}


def decide_layout(
    template: JsonDict,
    options: JsonDict,
    today: date,
    club_labels: JsonDict | None = None,
) -> JsonDict:
    """The layout to store on save, measured on the filled texts with the
    club's labels (the catalogue's when none are given), with the options,
    fact formats and labels it was measured with (R9b, R13, R14, R16, R17)."""
    all_labels = labels() if club_labels is None else club_labels
    document = printable(template)
    missing = missing_texts(document, all_labels)
    if missing:
        raise ApplicationIncompleteException(missing)
    unknown = unknown_gaps(template)
    if unknown:
        raise ApplicationUnknownFactException(unknown)
    formats = fact_formats()
    decided = check(filled(document, formats), all_labels, options, today)
    decided[FROZEN_OPTIONS] = options
    decided[FROZEN_FORMATS] = formats
    decided[FROZEN_LABELS] = all_labels
    return decided


def drawn_labels(stored: JsonDict | None, live: JsonDict | None) -> JsonDict:
    """The labels a form is drawn with: the stored layout's frozen copy, the
    catalogue for a layout saved before #133 (it was measured with it), or
    ``live`` (the club's, else the catalogue) for a draft."""
    if stored is not None:
        frozen: JsonDict | None = stored.get(FROZEN_LABELS)
        return frozen if frozen is not None else labels()
    return live if live is not None else labels()


def frozen_options(stored: JsonDict | None, live: JsonDict) -> JsonDict:
    """The profile options a form is drawn with: the stored layout's frozen
    copy, or ``live`` for a draft (R14)."""
    if stored is None:
        return live
    frozen: JsonDict = stored.get(FROZEN_OPTIONS, live)
    return frozen


def stored_layout(
    document: JsonDict,
    all_labels: JsonDict,
    options: JsonDict,
    today: date,
    stored: JsonDict,
) -> tuple[Layout, float]:
    """The stored layout and page 1's spread. A layout saved before every
    form printed the guardian block (#128) holds two spreads measured
    without its note: it keeps its layout and is spread again with it, so
    page 1 still fills and fits."""
    layout = layout_named(stored["name"])
    if layout is None:
        raise ValueError(f"unknown stored layout {stored['name']!r}")
    if stored.get(ONE_SPREAD):
        spread: float = stored["spread"]["with_guardian"]
        return layout, spread
    return layout, spread_of(document, all_labels, options, today, layout)


def _document(template: JsonDict, stored: JsonDict | None) -> JsonDict:
    """The template's texts, its gaps filled with the frozen formats."""
    formats: JsonDict = (stored or {}).get(FROZEN_FORMATS) or fact_formats()
    return filled(printable(template), formats)


def draw(
    template: JsonDict,
    member: JsonDict,
    options: JsonDict,
    logo: bytes | None,
    today: date,
    stored: JsonDict | None,
    preview: bool = False,
    publish_id: str | None = None,
    live_labels: JsonDict | None = None,
) -> bytes:
    """One form as PDF bytes, drawn in full.

    With a stored layout the form is drawn once, as stored, its facts
    printed with the formats frozen on save; without one (a draft's preview)
    it is measured for this member first (R14, R15a, R19).
    ``options`` are those ``frozen_options`` gives.
    """
    return bytes(
        form_builder(
            template,
            member,
            options,
            logo,
            today,
            stored,
            preview,
            publish_id,
            live_labels=live_labels,
        )
        .build()
        .output()
    )


def draw_blank(
    template: JsonDict,
    options: JsonDict,
    logo: bytes | None,
    today: date,
    stored: JsonDict,
    publish_id: str | None,
) -> tuple[bytes, JsonDict]:
    """A saved template's form with no member in it, as PDF bytes, and the
    places of every member value and tick in it (#128)."""
    b = form_builder(
        template, {}, options, logo, today, stored, False, publish_id, True
    )
    pdf = bytes(b.build().output())
    return pdf, b.places


def form_builder(
    template: JsonDict,
    member: JsonDict,
    options: JsonDict,
    logo: bytes | None,
    today: date,
    stored: JsonDict | None,
    preview: bool,
    publish_id: str | None,
    blank: bool = False,
    live_labels: JsonDict | None = None,
) -> ApplicationFormBuilder:
    """The builder that draws one form: from the stored layout, or measured
    for ``member`` when there is none (a draft's preview)."""
    document = _document(template, stored)
    all_labels = drawn_labels(stored, live_labels)
    if stored:
        layout, extra = stored_layout(document, all_labels, options, today, stored)
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
    return ApplicationFormBuilder(
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
        blank=blank,
    )
