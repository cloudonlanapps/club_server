"""A member's form from its template's stored drawing (#128, applications R25).

A published template's form is drawn once with no member in it, and the
place of each member value and tick recorded (``draw_blank``). A download
writes only the member's values, on a page per page of the drawing, and
lays those pages over it: each value fitted to its box with the same rule
as a full drawing (R18), each tick where the full drawing puts it.
"""

import io
from datetime import date

from pypdf import PdfReader, PdfWriter

from . import application_pdf_blocks as blocks
from .application_catalogue import JsonDict
from .application_pdf_builder import form_category
from .application_pdf_canvas import ApplicationPdfCanvas, Mark
from .application_pdf_layout import FOOTER_DAY, MEMBER_FOOTER


def overlay(
    places: JsonDict,
    template: JsonDict,
    member: JsonDict,
    options: JsonDict,
    today: date,
) -> tuple[ApplicationPdfCanvas, list[Mark]]:
    """The member's values alone, on as many pages as the drawing has, and
    the marks written. Refuses as a full drawing does when the member fits
    no age category, or chose one they do not fit (R11, R11a)."""
    values = blocks.field_values(member)
    ticked = blocks.ticks(member, options, form_category(template, member))
    pdf = ApplicationPdfCanvas(
        MEMBER_FOOTER.format(username=member["username"], day=f"{today:{FOOTER_DAY}}"),
        page_numbers=False,
        bold=False,
    )
    marks: list[Mark] = []
    fields: dict[str, JsonDict] = places["fields"]
    choices: dict[str, list[JsonDict]] = places["choices"]
    sign_date: JsonDict | None = places["sign_date"]
    for page in range(1, places["pages"] + 1):
        pdf.add_page()
        for key, place in fields.items():
            value = values.get(key)
            if place["page"] == page and value:
                lines, size = pdf.fit(value, place["width"], place["rows"])
                marks += pdf.write_lines(place, lines, size)
        for key, boxes in choices.items():
            for i in sorted(ticked.get(key) or set()):
                box = boxes[i]
                if box["page"] == page:
                    pdf.tick(box["x"], box["y"])
                    marks.append(("tick", page, box["x"], box["y"]))
        if sign_date and sign_date["page"] == page:
            marks.append(pdf.write_date(sign_date, today))
    return pdf, marks


def merge(drawing: bytes, values: bytes) -> bytes:
    """The ``values`` pages laid over the ``drawing``'s, page by page."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(drawing)))
    for page, top in zip(writer.pages, PdfReader(io.BytesIO(values)).pages):
        page.merge_page(top)
    out = io.BytesIO()
    _ = writer.write(out)
    return out.getvalue()


def fill(
    drawing: bytes,
    places: JsonDict,
    template: JsonDict,
    member: JsonDict,
    options: JsonDict,
    today: date,
) -> bytes:
    """``member``'s form: their values written over the stored ``drawing``."""
    pdf, _marks = overlay(places, template, member, options, today)
    return merge(drawing, bytes(pdf.output()))
