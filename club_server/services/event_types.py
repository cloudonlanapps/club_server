"""The one place an event's type is compared (#389).

Each type has its own service — ``ProgrammeService``, ``CampService``,
``OneOffService`` — holding only that type's rules, so none of them has
another type's cases to skip. Routers ask this module which verbs an event
offers and which service answers for it; nothing else branches on the type.
"""

from enum import StrEnum

from fastapi import HTTPException, status

from ..db.models.event import Event

PROGRAMME = "programme"
CAMP = "camp"
ONE_OFF = "oneOff"

EVENT_TYPES = (PROGRAMME, CAMP, ONE_OFF)


class EventVerb(StrEnum):
    """Every type-specific verb a router gates on."""

    update = "update"
    correction = "correction"
    future_split = "split"
    terminate = "terminate"
    extend = "extend"
    cancel = "cancel"
    reschedule = "reschedule"
    drop = "drop"
    reinstate = "reinstate"
    assign_trial = "assign_trial"
    reschedule_occurrence = "reschedule_occurrence"


_VERBS: dict[str, frozenset[EventVerb]] = {
    PROGRAMME: frozenset(
        {
            EventVerb.correction,
            EventVerb.future_split,
            EventVerb.terminate,
            EventVerb.extend,
            EventVerb.assign_trial,
            EventVerb.reschedule_occurrence,
        }
    ),
    CAMP: frozenset(
        {
            EventVerb.update,
            EventVerb.cancel,
            EventVerb.reschedule,
            EventVerb.reschedule_occurrence,
        }
    ),
    ONE_OFF: frozenset(
        {EventVerb.update, EventVerb.reschedule, EventVerb.drop, EventVerb.reinstate}
    ),
}


def is_programme(event: Event) -> bool:
    """Whether ``event`` is a programme — the only type credit applies to."""
    return event.type == PROGRAMME


def is_camp(event: Event) -> bool:
    """Whether ``event`` is a camp."""
    return event.type == CAMP


def is_one_off(event: Event) -> bool:
    """Whether ``event`` is a one-off."""
    return event.type == ONE_OFF


def blocks_on_conflict(event_type: str) -> bool:
    """Programme × programme clashes block; every other pairing reports (R30, R30c)."""
    return event_type == PROGRAMME


def is_bounded_type(event_type: str) -> bool:
    """Camps and one-offs are finite and sit under the scheduling horizon (one-off R20a)."""
    return event_type != PROGRAMME


def validate_rrule_for(event_type: str, rrule: str | None) -> None:
    """Each type's own recurrence shape, applied wherever a schedule is written."""
    from ..exceptions import InvalidRruleException
    from .camp import validate_camp_rrule
    from .oneoff import validate_oneoff_rrule
    from .programme import validate_programme_rrule
    from .rrule import validate_rrule

    if rrule and not validate_rrule(rrule):
        raise InvalidRruleException(rrule)
    if event_type == PROGRAMME:
        validate_programme_rrule(rrule)
    elif event_type == CAMP:
        if rrule is not None:
            validate_camp_rrule(rrule)
    elif event_type == ONE_OFF:
        validate_oneoff_rrule(rrule)


def offers(event: Event, verb: EventVerb) -> bool:
    """Whether ``event``'s type offers ``verb`` (lifecycle L9)."""
    return verb in _VERBS.get(event.type, frozenset())


def types_offering(verb: EventVerb) -> list[str]:
    """The types that offer ``verb``, for error messages."""
    return [t for t in EVENT_TYPES if verb in _VERBS[t]]


def require_verb(event: Event, verb: EventVerb) -> None:
    """Raise 400 ``INVALID_EVENT_TYPE`` unless the event's type offers ``verb``."""
    if offers(event, verb):
        return
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "INVALID_EVENT_TYPE",
            "message": (
                f"This operation is not supported for event type '{event.type}'. "
                f"Allowed types: {types_offering(verb)}"
            ),
        },
    )


def require_reschedulable(event: Event) -> None:
    """Raise 400 ``EVENT_TYPE_NOT_SUPPORTED`` for a programme: it is split, never rescheduled (R26)."""
    if offers(event, EventVerb.reschedule):
        return
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "EVENT_TYPE_NOT_SUPPORTED",
            "message": (
                f"Reschedule is supported only for camps and one-offs; got "
                f"'{event.type}'."
            ),
        },
    )
