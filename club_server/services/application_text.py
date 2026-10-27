"""Facts, gaps and a template's printed texts (#93, #107, #126).

A template's printed texts are custom text in each language, and may hold
``{name}`` gaps naming its facts. They are stored as written; save refuses a
gap naming no fact (``unknown_gaps``, applications R9b) and the form fills
each gap from the facts, formatted per language with the clause library's
``formats`` and ``choices`` (``filled``, R7, R15a). Ported from the
prototype; its exits are domain errors here.

The template given here is the form's own document (``application_document``):
snake-case keys and ISO dates, as the prototype read them.
"""

import re
from datetime import date
from typing import Any

from ..exceptions import ApplicationReferenceException
from .application_catalogue import JsonDict
from .application_pdf_layout import ENGLISH

# Day parts: a time before 12, 16, 20 or after.
NOON, AFTERNOON_END, EVENING_END = 12, 16, 20
HALF_DAY = 12
THOUSANDS = 3
INDIAN_GROUP = 2

# A gap: "{name}", the name as a fact's (#126). Other brace text is literal.
GAP = re.compile(r"\{([a-z][a-z0-9_]*)\}")
# The texts of a template that are one text each, in the order printed;
# the header items and selections print after the first three.
SINGLE_TEXTS = ("organisation", "title", "subtitle", "declaration", "form_footer")
TERM_PARTS = ("heading", "body")
NOTE_PARTS = ("text", "attribution")


def fmt_day(d: date, lang: str, lib: JsonDict) -> str:
    """A date in ``lang``: "19 November 2026"."""
    return f"{d.day} {lib['formats']['months'][lang][d.month - 1]} {d.year}"


def fmt_range(start: date, end: date, lang: str, lib: JsonDict) -> str:
    """A range in ``lang``: "1–15 November 2026", "1 ते 15 नोव्हेंबर 2026"."""
    months = lib["formats"]["months"][lang]
    word = lib["formats"]["range_word"][lang]
    if word == "–" and (start.month, start.year) != (end.month, end.year):
        word = " – "
    if (start.month, start.year) == (end.month, end.year):
        return f"{start.day}{word}{end.day} {months[end.month - 1]} {end.year}"
    if start.year == end.year:
        return f"{start.day} {months[start.month - 1]}{word}{end.day} {months[end.month - 1]} {end.year}"
    return f"{fmt_day(start, lang, lib)}{word}{fmt_day(end, lang, lib)}"


def fmt_time(value: str, lang: str, lib: JsonDict) -> str:
    """A time with its part of the day: "6:00 am", "सकाळी 6:00"."""
    h, m = (int(x) for x in value.split(":"))
    part = lib["formats"]["day_parts"][lang][
        0 if h < NOON else 1 if h < AFTERNOON_END else 2 if h < EVENING_END else 3
    ]
    if lang == ENGLISH:
        return f"{(h - 1) % HALF_DAY + 1}:{m:02d} {part}"
    return f"{part} {h}:{m:02d}"


def fmt_amount(value: Any) -> str:
    """Indian grouping: 1,50,000."""
    digits = str(int(value))
    head, tail = digits[:-THOUSANDS], digits[-THOUSANDS:]
    groups: list[str] = []
    while len(head) > INDIAN_GROUP:
        groups.insert(0, head[-INDIAN_GROUP:])
        head = head[:-INDIAN_GROUP]
    return (
        ",".join([g for g in [head, *groups] if g] + [tail]) if head or groups else tail
    )


def fact_text(fact: JsonDict, lang: str, lib: JsonDict) -> str:
    """One fact printed in ``lang``."""
    kind = fact["type"]
    if kind == "date":
        return fmt_day(date.fromisoformat(fact["value"]), lang, lib)
    if kind == "date_range":
        return fmt_range(
            date.fromisoformat(fact["from"]), date.fromisoformat(fact["to"]), lang, lib
        )
    if kind == "time":
        return fmt_time(fact["value"], lang, lib)
    if kind == "amount":
        return fmt_amount(fact["value"])
    if kind == "choices":
        unknown = [c for c in fact["value"] if c not in lib["choices"]]
        if unknown:
            raise ApplicationReferenceException(
                f"no choice {unknown[0]!r} in the library"
            )
        words = [lib["choices"][c][lang] for c in fact["value"]]
        joiner = lib["formats"]["and"][lang]
        return (
            words[0]
            if len(words) == 1
            else f"{', '.join(words[:-1])} {joiner} {words[-1]}"
        )
    if kind == "name":  # a name is printed as typed, or as given for a language
        v = fact["value"]
        return v if isinstance(v, str) else v.get(lang) or v[ENGLISH]
    raise ApplicationReferenceException(f"unknown fact type {kind!r}")


def fill_text(text: str, lang: str, facts: JsonDict, lib: JsonDict) -> str:
    """``text`` with each gap naming one of ``facts`` filled in ``lang``
    (applications R15a); any other brace text is left as written."""

    def fact_for(match: re.Match[str]) -> str:
        name = match.group(1)
        return fact_text(facts[name], lang, lib) if name in facts else match.group(0)

    return GAP.sub(fact_for, text)


def _filled_text(
    value: dict[str, str] | None, facts: JsonDict, lib: JsonDict
) -> dict[str, str] | None:
    if not value:
        return value
    return {lang: fill_text(text, lang, facts, lib) for lang, text in value.items()}


def filled(document: JsonDict, lib: JsonDict) -> JsonDict:
    """A printable template with every gap of its printed texts filled from
    its facts, each in its text's language (R15a). Label overrides and
    age-category labels print as written."""
    facts: JsonDict = document.get("facts") or {}
    out = dict(document)
    for key in SINGLE_TEXTS:
        out[key] = _filled_text(document.get(key), facts, lib)
    for key in ("header", "selections"):
        texts: list[dict[str, str] | None] = document.get(key) or []
        out[key] = [_filled_text(t, facts, lib) for t in texts]
    terms: list[JsonDict] = document.get("terms") or []
    out["terms"] = [
        {part: _filled_text(term.get(part), facts, lib) for part in TERM_PARTS}
        for term in terms
    ]
    notes: list[JsonDict] = document.get("terms_notes") or []
    out["terms_notes"] = [
        {part: _filled_text(note.get(part), facts, lib) for part in NOTE_PARTS}
        for note in notes
    ]
    return out


def _printed_texts(template: JsonDict) -> list[tuple[str, dict[str, str]]]:
    """Every text a gap may sit in, by its place, in the order printed."""
    texts: list[tuple[str, Any]] = [
        (key, template.get(key)) for key in SINGLE_TEXTS[:3]
    ]
    for i, item in enumerate(template.get("header") or []):
        if isinstance(item, list):
            lines: list[Any] = item  # pyright: ignore[reportUnknownVariableType]
            texts.extend((f"header[{i}][{j}]", line) for j, line in enumerate(lines))
        else:
            texts.append((f"header[{i}]", item))
    texts.extend(
        (f"selections[{i}]", s) for i, s in enumerate(template.get("selections") or [])
    )
    texts.extend((key, template.get(key)) for key in SINGLE_TEXTS[3:])
    for i, term in enumerate(template.get("terms") or []):
        texts.extend((f"terms[{i}].{part}", term.get(part)) for part in TERM_PARTS)
    for i, note in enumerate(template.get("terms_notes") or []):
        texts.extend(
            (f"terms_notes[{i}].{part}", note.get(part)) for part in NOTE_PARTS
        )
    return [(place, text) for place, text in texts if text]


def unknown_gaps(template: JsonDict) -> list[dict[str, str]]:
    """Each gap naming no fact of ``template``, as ``{place, language, gap}``,
    in the order printed: by place, then the print languages before any
    other, then as the text reads; a gap repeated in one text once (R9b).

    ``template`` is the form's document, its header items not yet joined.
    """
    facts: JsonDict = template.get("facts") or {}
    langs: list[str] = template.get("languages") or [ENGLISH]
    found: list[dict[str, str]] = []
    for place, text in _printed_texts(template):
        ordered = [lang for lang in langs if lang in text]
        ordered += [lang for lang in text if lang not in langs]
        for lang in ordered:
            names = dict.fromkeys(GAP.findall(text[lang]))
            found.extend(
                {"place": place, "language": lang, "gap": name}
                for name in names
                if name not in facts
            )
    return found


def _joined(value: Any) -> dict[str, str] | None:
    """A custom text, or a header item's list of them joined one per line."""
    if not isinstance(value, list):
        text: dict[str, str] | None = value
        return text
    parts: list[dict[str, str]] = [p for p in value if p]  # pyright: ignore[reportUnknownVariableType]
    langs = list(dict.fromkeys(lang for part in parts for lang in part))
    return {
        lang: "\n".join(part[lang] for part in parts if part.get(lang))
        for lang in langs
    }


def printable(template: JsonDict) -> JsonDict:
    """The template's texts as the form prints them (#107).

    Every text is custom text already; nothing is read from the clause
    library. A header item's lines are joined, English term headings are
    numbered as on paper forms, and each note carries its attribution.
    """
    out = dict(template)
    header: list[Any] = template.get("header") or []
    out["header"] = [_joined(h) for h in header]
    out["selections"] = list(template.get("selections") or [])
    terms: list[JsonDict] = []
    for i, item in enumerate(template.get("terms") or [], 1):
        heading: dict[str, str] = dict(item.get("heading") or {})
        if heading.get(ENGLISH):
            heading[ENGLISH] = f"{i}. {heading[ENGLISH]}"
        terms.append({"heading": heading, "body": item.get("body")})
    out["terms"] = terms
    notes: list[JsonDict] = template.get("terms_notes") or []
    out["terms_notes"] = [
        {"text": n.get("text"), "attribution": n.get("attribution")} for n in notes
    ]
    return out


# Texts every print language must have before a template can be saved, as
# the API names them. The rest (header items, age labels) may stay English
# only: names and numbers. Each of these must be there at all.
REQUIRED_TEXTS = {
    "title": "title",
    "declaration": "declaration",
    "form_footer": "formFooter",
}
PRESENT_TEXTS = frozenset(REQUIRED_TEXTS)


def lacking(
    path: str, value: dict[str, str] | None, langs: list[str], present: bool = False
) -> JsonDict | None:
    """``{"field": path, "languages": [...]}`` when ``value`` lacks print
    languages; a required text missing altogether lacks every one."""
    if not value:
        return {"field": path, "languages": list(langs)} if present else None
    absent = [lang for lang in langs if not value.get(lang)]
    return {"field": path, "languages": absent} if absent else None


def incomplete(template: JsonDict) -> list[JsonDict]:
    """Each text that needs a print language it lacks, with the languages (R9).

    ``template`` is printable. The fields name the API's paths
    (``termsNotes[0].text``).
    """
    langs: list[str] = template["languages"]
    found: list[JsonDict] = []

    def need(path: str, value: dict[str, str] | None, present: bool = False):
        entry = lacking(path, value, langs, present)
        if entry:
            found.append(entry)

    for key, path in REQUIRED_TEXTS.items():
        need(path, template.get(key), present=key in PRESENT_TEXTS)
    for i, item in enumerate(template.get("terms") or []):
        need(f"terms[{i}].heading", item.get("heading"))
        need(f"terms[{i}].body", item.get("body"))
    for i, note in enumerate(template.get("terms_notes") or []):
        need(f"termsNotes[{i}].text", note.get("text"))
    for i, sel in enumerate(template.get("selections") or []):
        need(f"selections[{i}]", sel)
    return found
