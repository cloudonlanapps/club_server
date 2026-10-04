"""Dates and numbers as the PDF writes them (#535, R63)."""

from datetime import datetime, timezone

from .evaluation_pdf_layout import DATE_FORMAT, MONTHS, MS_PER_SECOND


def date_text(epoch_ms: int | None) -> str:
    """A UTC date as "5 Jan 2026", or "" for none."""
    if epoch_ms is None:
        return ""
    day = datetime.fromtimestamp(epoch_ms / MS_PER_SECOND, tz=timezone.utc)
    return DATE_FORMAT.format(day=day.day, month=MONTHS[day.month - 1], year=day.year)


def number_text(value: float) -> str:
    """A number without a needless decimal: 12, 12.5."""
    return str(int(value)) if value == int(value) else f"{value:g}"
