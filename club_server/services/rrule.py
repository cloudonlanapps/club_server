import re
from datetime import datetime, timedelta, timezone

from dateutil.rrule import rrule, rrulestr


def parse_rrule_with_exdates(combined: str) -> tuple[str, list[datetime]]:
    """Parse a combined RRULE+EXDATE string.

    Input format (newline separated):
        FREQ=DAILY;COUNT=7
        EXDATE:20240115T090000Z,20240117T090000Z

    Returns:
        Tuple of (rrule_string, list_of_excluded_dates)
    """
    if not combined:
        return ("", [])

    lines = combined.strip().split("\n")
    rrule_part = ""
    exdates: list[datetime] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("RRULE:"):
            rrule_part = line[6:]
        elif line.startswith("FREQ="):
            rrule_part = line
        elif line.startswith("EXDATE:"):
            exdates = parse_exdates(line[7:])

    return (rrule_part, exdates)


def parse_exdates(exdate_string: str) -> list[datetime]:
    """Parse EXDATE values from a comma-separated string.

    Format: 20240115T090000Z,20240117T090000Z
    """
    if not exdate_string:
        return []

    dates: list[datetime] = []
    for date_str in exdate_string.split(","):
        date_str = date_str.strip()
        if not date_str:
            continue
        try:
            if "T" in date_str:
                # Parse yyyyMMddTHHmmssZ format (UTC per the trailing Z).
                dt = datetime.strptime(date_str, "%Y%m%dT%H%M%SZ").replace(
                    tzinfo=timezone.utc
                )
                dates.append(dt)
            else:
                # Date-only format — treat as midnight UTC.
                dt = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
                dates.append(dt)
        except ValueError:
            pass  # Skip invalid dates

    return dates


def extract_count_from_rrule(rrule_string: str) -> int | None:
    """Extract COUNT value from RRULE string if present."""
    match = re.search(r"COUNT=(\d+)", rrule_string)
    if match:
        return int(match.group(1))
    return None


def remove_count_from_rrule(rrule_string: str) -> str:
    """Remove COUNT from RRULE string to allow extended generation."""
    # Remove COUNT=N with optional preceding or following semicolon
    result = re.sub(r";?COUNT=\d+;?", ";", rrule_string)
    # Clean up any leading/trailing semicolons
    result = result.strip(";")
    # Fix double semicolons
    result = re.sub(r";+", ";", result)
    return result


def generate_occurrences(
    rrule_string: str | None,
    start_time_utc: datetime,
    end_time_utc: datetime,
    until_time_utc: datetime | None = None,
    from_time_utc: datetime | None = None,
    to_time_utc: datetime | None = None,
    max_count: int = 365,
) -> list[datetime]:
    """Generate occurrence times from an RRULE string.

    Args:
        rrule_string: RFC 5545 RRULE string, optionally with EXDATE on new line
            (e.g., "FREQ=DAILY;COUNT=6\\nEXDATE:20240115T090000Z")
        start_time_utc: Start time of the event (used as DTSTART)
        end_time_utc: End time of the event (for calculating duration)
        until_time_utc: Optional explicit end date for the series
        from_time_utc: Optional filter - only return occurrences after this time
        to_time_utc: Optional filter - only return occurrences before this time
        max_count: Maximum number of occurrences to generate (default: 365)

    Note on COUNT and EXDATE behavior:
        When both COUNT and EXDATE are present, COUNT represents the number
        of actual sessions needed (not including excluded dates). The generation
        extends beyond the calendar days implied by COUNT to deliver the
        requested number of sessions.

    Returns:
        List of occurrence start times
    """
    if not rrule_string:
        if from_time_utc and start_time_utc < from_time_utc:
            return []
        if to_time_utc and start_time_utc >= to_time_utc:
            return []
        return [start_time_utc]

    # Parse combined RRULE+EXDATE format
    rrule_part, exclude_dates = parse_rrule_with_exdates(rrule_string)
    if not rrule_part:
        if from_time_utc and start_time_utc < from_time_utc:
            return []
        if to_time_utc and start_time_utc >= to_time_utc:
            return []
        return [start_time_utc]

    exclude_set = set(exclude_dates)

    # Handle COUNT + EXDATE: COUNT means actual sessions needed
    original_count = extract_count_from_rrule(rrule_part)
    effective_rrule = rrule_part

    if original_count is not None and exclude_dates:
        # Remove COUNT to allow extended generation
        effective_rrule = remove_count_from_rrule(rrule_part)

    rule = rrulestr(effective_rrule, dtstart=start_time_utc)

    if until_time_utc and isinstance(rule, rrule):
        # RFC 5545: a rule cannot carry both COUNT and UNTIL. COUNT is still
        # honored by the original_count check in the loop below, so drop it
        # from the rule before applying UNTIL (e.g. a cancelled COUNT series).
        rule = rule.replace(count=None, until=until_time_utc)

    filter_after = (
        from_time_utc if from_time_utc else start_time_utc - timedelta(days=1)
    )
    filter_before = to_time_utc if to_time_utc else None

    occurrences: list[datetime] = []
    total_generated = 0  # Count actual sessions (excluding EXDATE)

    for occurrence in rule:
        # Skip excluded dates
        if occurrence in exclude_set:
            continue

        total_generated += 1

        # Enforce original COUNT (actual sessions, not calendar days)
        if original_count is not None and total_generated > original_count:
            break

        if len(occurrences) >= max_count:
            break

        if occurrence < filter_after:
            continue

        # Half-open [from, to): an occurrence starting exactly on the upper
        # bound belongs to the next range, matching the non-recurring branch
        # above and the horizon clipping in services/conflict.py.
        if filter_before and occurrence >= filter_before:
            break

        occurrences.append(occurrence)

    return occurrences


def validate_rrule(rrule_string: str) -> bool:
    """Validate an RRULE string.

    Args:
        rrule_string: The RRULE string to validate (may include EXDATE)

    Returns:
        True if valid, False otherwise
    """
    try:
        # Parse combined format to get just the RRULE part
        rrule_part, _ = parse_rrule_with_exdates(rrule_string)
        if not rrule_part:
            return False
        _ = rrulestr(rrule_part, dtstart=datetime.now(timezone.utc))
        return True
    except (ValueError, KeyError):
        return False


def get_next_occurrence(
    rrule_string: str | None,
    start_time_utc: datetime,
    after_time_utc: datetime,
    until_time_utc: datetime | None = None,
) -> datetime | None:
    """Get the next occurrence after a given time.

    Args:
        rrule_string: RFC 5545 RRULE string (may include EXDATE)
        start_time_utc: Start time of the event
        after_time_utc: Time to search after
        until_time_utc: Optional end date for the series

    Returns:
        Next occurrence time or None if no more occurrences
    """
    if not rrule_string:
        if start_time_utc > after_time_utc:
            return start_time_utc
        return None

    # Parse combined format
    rrule_part, exclude_dates = parse_rrule_with_exdates(rrule_string)
    if not rrule_part:
        if start_time_utc > after_time_utc:
            return start_time_utc
        return None

    exclude_set = set(exclude_dates)

    # Handle COUNT + EXDATE
    original_count = extract_count_from_rrule(rrule_part)
    effective_rrule = rrule_part

    if original_count is not None and exclude_dates:
        effective_rrule = remove_count_from_rrule(rrule_part)

    rule = rrulestr(effective_rrule, dtstart=start_time_utc)

    if until_time_utc and isinstance(rule, rrule):
        # RFC 5545: a rule cannot carry both COUNT and UNTIL. COUNT is still
        # honored by the original_count check in the loop below, so drop it
        # from the rule before applying UNTIL (e.g. a cancelled COUNT series).
        rule = rule.replace(count=None, until=until_time_utc)

    # Iterate to find next valid occurrence (skipping excluded dates)
    total_generated = 0
    for occurrence in rule:
        if occurrence in exclude_set:
            continue

        total_generated += 1

        # Enforce original COUNT
        if original_count is not None and total_generated > original_count:
            return None

        if occurrence > after_time_utc:
            return occurrence

    return None


def calculate_occurrence_end_time(
    occurrence_start: datetime,
    event_start: datetime,
    event_end: datetime,
) -> datetime:
    """Calculate end time for an occurrence based on event duration.

    Args:
        occurrence_start: Start time of the specific occurrence
        event_start: Original event start time
        event_end: Original event end time

    Returns:
        End time for the occurrence
    """
    duration = event_end - event_start
    return occurrence_start + duration


def compute_last_occurrence_end(
    start_time_ms: int,
    end_time_ms: int,
    rrule_string: str | None,
) -> datetime:
    """Compute the end time of the last occurrence, handling EXDATE correctly.

    Uses generate_occurrences() which properly handles:
    - COUNT with EXDATE: COUNT=6 with 1 EXDATE = 6 sessions over 7 calendar days
    - The last occurrence is the final session date

    Args:
        start_time_ms: Event start time in milliseconds since epoch
        end_time_ms: Event end time in milliseconds since epoch
        rrule_string: RRULE string (may include EXDATE)

    Returns:
        End datetime of the last occurrence (timezone-aware UTC)
    """
    from datetime import timezone

    start_time = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
    end_time = datetime.fromtimestamp(end_time_ms / 1000, tz=timezone.utc)

    if not rrule_string:
        return end_time

    # Use generate_occurrences which handles EXDATE correctly
    occurrences = generate_occurrences(
        rrule_string=rrule_string,
        start_time_utc=start_time,
        end_time_utc=end_time,
    )

    if not occurrences:
        return end_time

    # Get the last occurrence and calculate its end time
    last_occurrence_start = occurrences[-1]
    return calculate_occurrence_end_time(last_occurrence_start, start_time, end_time)


def compute_is_registration_closed(registration_deadline_ms: int | None) -> bool:
    """Compute if registration deadline has passed.

    Args:
        registration_deadline_ms: Registration deadline in milliseconds since epoch, or None

    Returns:
        True if deadline has passed, False if no deadline or deadline not passed
    """
    from datetime import timezone

    if not registration_deadline_ms:
        return False
    now = datetime.now(timezone.utc)
    deadline = datetime.fromtimestamp(registration_deadline_ms / 1000, tz=timezone.utc)
    return now > deadline
