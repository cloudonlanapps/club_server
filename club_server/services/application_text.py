"""Clauses and facts: a template's texts resolved per language (#93).

A template states its facts once (dates, venue, phone, …) and builds its
header items, declaration, rules, selections and notes from library clauses
whose gaps name those facts, or from custom text in each language. Resolving
turns every reference into plain text per language before the form is laid
out (applications R6–R8). Ported from the prototype; its exits are domain
errors here.

The template given here is the form's own document (``application_document``):
snake-case keys and ISO dates, as the prototype read them.
"""

from datetime import date
from typing import Any

from ..exceptions import ApplicationReferenceException
from .application_catalogue import JsonDict
from .application_pdf_layout import ENGLISH, SEPARATOR

# Day parts: a time before 12, 16, 20 or after.
NOON, AFTERNOON_END, EVENING_END = 12, 16, 20
HALF_DAY = 12
THOUSANDS = 3
INDIAN_GROUP = 2


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


def resolve(template: JsonDict, lib: JsonDict) -> JsonDict:
    """The template with every clause and fact reference turned into text in
    each of the library's languages."""
    facts: JsonDict = template.get("facts") or {}
    clauses: JsonDict = lib["clauses"]
    langs = list(lib["formats"]["months"])

    def fact(name: str, expected: str | None = None) -> dict[str, str]:
        if name not in facts:
            raise ApplicationReferenceException(f"no fact named {name!r}")
        if expected and facts[name]["type"] != expected:
            raise ApplicationReferenceException(
                f"fact {name!r} is a {facts[name]['type']}, the clause needs a {expected}"
            )
        return {lang: fact_text(facts[name], lang, lib) for lang in langs}

    def clause(ref: JsonDict, part: str = "text") -> dict[str, str]:
        c: JsonDict | None = clauses.get(ref["clause"])
        if c is None:
            raise ApplicationReferenceException(
                f"no clause {ref['clause']!r} in the library"
            )
        if part not in c:
            raise ApplicationReferenceException(
                f"clause {ref['clause']!r} has no {part}, so it cannot be a rule"
            )
        args: JsonDict = ref.get("args") or {}
        gaps: JsonDict = c.get("gaps", {})
        missing = set(gaps) - set(args)
        if missing:
            raise ApplicationReferenceException(
                f"clause {ref['clause']!r} needs facts for {', '.join(sorted(missing))}"
            )
        values = {gap: fact(args[gap], kind) for gap, kind in gaps.items()}
        return {
            lang: c[part][lang].format(**{g: v[lang] for g, v in values.items()})
            for lang in c[part]
        }

    def text(value: Any, joiner: str = SEPARATOR) -> dict[str, str] | None:
        """A plain {lang: text}, a clause, a fact, or a list of them joined."""
        if value is None:
            return None
        if isinstance(value, list):
            parts = [text(v) or {} for v in value]  # pyright: ignore[reportUnknownVariableType]
            return {
                lang: joiner.join(p[lang] for p in parts if p.get(lang))
                for lang in langs
            }
        if "clause" in value:
            return clause(value)
        if "fact" in value:
            return fact(value["fact"])
        plain: dict[str, str] = value
        return plain

    out = dict(template)
    for key in ("organisation", "title", "subtitle", "declaration", "form_footer"):
        out[key] = text(template.get(key))
    header: list[Any] = template.get("header") or []
    selections: list[Any] = template.get("selections") or []
    items: list[JsonDict] = template.get("terms") or []
    notes: list[JsonDict] = template.get("terms_notes") or []
    out["header"] = [text(h, joiner="\n") for h in header]  # one line each
    out["selections"] = [text(s) for s in selections]
    terms: list[JsonDict] = []
    for i, item in enumerate(items, 1):
        if "clause" in item:
            item = {"heading": clause(item, "heading"), "body": clause(item)}
        heading: dict[str, str] = dict(item.get("heading") or {})
        if heading.get(ENGLISH):  # English headings are numbered, as on paper forms
            heading[ENGLISH] = f"{i}. {heading[ENGLISH]}"
        terms.append({"heading": heading, "body": item.get("body")})
    out["terms"] = terms
    out["terms_notes"] = [
        {
            "text": text(n["text"] if "text" in n else n),
            "attribution": text(n.get("attribution")),
        }
        for n in notes
    ]
    return out


# Texts every print language must have before a template can be saved, as
# the API names them. The rest (header items, age labels) may stay English
# only: names and numbers. The title and the declaration must be there at all.
REQUIRED_TEXTS = {
    "title": "title",
    "declaration": "declaration",
    "form_footer": "formFooter",
}
PRESENT_TEXTS = frozenset({"title", "declaration"})


def incomplete(template: JsonDict) -> list[str]:
    """Where a print language is missing from a text that needs it (R9).

    ``template`` is resolved. The paths name the API's fields
    (``termsNotes[0].text``).
    """
    langs: list[str] = template["languages"]
    found: list[str] = []

    def need(path: str, value: dict[str, str] | None, present: bool = False):
        if (present and not value) or (
            value and any(not value.get(lang) for lang in langs)
        ):
            found.append(path)

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
