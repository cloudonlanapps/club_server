"""Info markdown as runs of plain text and links (#535, R63).

The markdown is rendered to HTML first, so emphasis is recognised by the
markdown rules (``snake_case`` and ``5 * 3`` keep their characters) and not
by stripping marker characters. The HTML is then read as the prototype's
``survey_html_segments.dart`` reads an info block: an anchor becomes a link
keeping its text, every other tag is dropped, a block end is a line break,
entities are decoded and blank runs collapse.
"""

import html
import re
from typing import NamedTuple

import markdown as markdown_lib


class PdfTextRun(NamedTuple):
    """A run of text, linked when ``link`` is set."""

    text: str
    link: str | None = None


# An anchor with a double- or single-quoted href: groups 1 or 2 hold the
# link, group 3 the content.
_ANCHOR = re.compile(
    r"""<a\s[^>]*?href\s*=\s*(?:"([^"]*)"|'([^']*)')[^>]*>(.*?)</a\s*>""",
    re.IGNORECASE | re.DOTALL,
)
# Block-level tags that end a line.
_BLOCK_END = re.compile(r"<\s*(br|/p|/div|/li|/h\d)\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]*>")
_BLANKS = re.compile(r"[ \t\r]+")
_LINE_BREAKS = re.compile(r" ?\n[ \n]*")


def _fragment_text(fragment: str) -> str:
    """The text of an HTML fragment; outer spaces kept for the runs around a link."""
    text = html.unescape(_TAG.sub("", _BLOCK_END.sub("\n", fragment)))
    return _LINE_BREAKS.sub("\n", _BLANKS.sub(" ", text))


def _trimmed(runs: list[PdfTextRun]) -> list[PdfTextRun]:
    """``runs`` without empty plain runs, trimmed at both ends."""
    kept = [r for r in runs if r.link or r.text]
    if kept and not kept[0].link:
        kept[0] = PdfTextRun(kept[0].text.lstrip())
    if kept and not kept[-1].link:
        kept[-1] = PdfTextRun(kept[-1].text.rstrip())
    return [r for r in kept if r.link or r.text]


def markdown_runs(markdown: str) -> list[PdfTextRun]:
    """``markdown`` as plain text and links, its formatting dropped."""
    rendered = markdown_lib.markdown(markdown)
    runs: list[PdfTextRun] = []
    start = 0
    for match in _ANCHOR.finditer(rendered):
        runs.append(PdfTextRun(_fragment_text(rendered[start : match.start()])))
        href = html.unescape(match.group(1) or match.group(2) or "")
        text = _fragment_text(match.group(3) or "").strip()
        runs.append(PdfTextRun(text or href, href))
        start = match.end()
    runs.append(PdfTextRun(_fragment_text(rendered[start:])))
    return _trimmed(runs)
