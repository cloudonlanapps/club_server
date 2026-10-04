"""Splitting the PDF's blocks into pages (#535, R63).

Ported from the prototype's ``survey_pdf_paginate.dart``.
"""


def _section_height(heights: list[float], sections: list[int | None], i: int) -> float:
    """The height of the run of blocks from ``i`` sharing its section."""
    total = 0.0
    j = i
    while j < len(heights) and sections[j] == sections[i]:
        total += heights[j]
        j += 1
    return total


def paginate(
    heights: list[float],
    keep_with_next: list[bool],
    sections: list[int | None],
    capacity: float,
) -> list[list[int]]:
    """Each page's block indexes, in order, at most ``capacity`` high each.

    - A block marked in ``keep_with_next`` never ends a page while a block
      follows it: it moves to the next page with that block.
    - Blocks sharing a ``sections`` id form a section. A section that does
      not fit in what is left of a page, but fits on an empty one, moves
      whole to the next page; sections otherwise share pages as far as they
      fit. A section taller than a page breaks between its blocks.
    - A block taller than a page gets a page of its own.
    """
    pages: list[list[int]] = []
    page: list[int] = []
    used = 0.0
    i = 0
    while i < len(heights):
        section = sections[i]
        starts_section = section is not None and (i == 0 or sections[i - 1] != section)
        if starts_section and page:
            needed = _section_height(heights, sections, i)
            if used + needed > capacity and needed <= capacity:
                pages.append(page)
                page, used = [], 0.0
        # The run that must stay together: kept blocks and the one after.
        end = i
        while end < len(heights) - 1 and keep_with_next[end]:
            end += 1
        run = sum(heights[i : end + 1])
        if page and used + run > capacity:
            pages.append(page)
            page, used = [], 0.0
        page.extend(range(i, end + 1))
        used += run
        i = end + 1
    if page:
        pages.append(page)
    return pages
