"""The scheduling code is split by event type (#389).

Each type's rules live in its own service, so a camp service has no
programme cases to skip. The evidence is the absence of event-type branches
from the scheduling modules: the only place a type is compared is the
dispatcher that picks the service.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent / "club_server"

SCHEDULING_MODULES = (
    "services/event.py",
    "services/programme.py",
    "services/camp.py",
    "services/oneoff.py",
    "services/schedule.py",
    "services/occurrence.py",
    "services/rrule.py",
    "services/conflict_gates.py",
    "routers/events.py",
    "routers/event_lifecycle.py",
    "routers/occurrences.py",
)

DISPATCHER = "services/event_types.py"

_TYPE_CHECK = re.compile(
    r"""(?<!Event)\.type\s*(?:==|!=|\bin\b|\bnot\s+in\b)"""
    r"""|(?:==|!=)\s*["'](?:camp|programme|oneOff)["']"""
)
"""An event-type branch. ``Event.type == x`` is a listing filter on the
column, not a branch, and is left alone."""


def _type_checks(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if _TYPE_CHECK.search(line) and not line.strip().startswith("#")
    ]


def test_should_have_one_service_per_type_behind_a_dispatcher():
    for name in ("programme", "camp", "oneoff", "schedule"):
        assert (ROOT / "services" / f"{name}.py").exists(), name
    assert (ROOT / DISPATCHER).exists()


def test_should_compare_event_types_only_in_the_dispatcher():
    offenders = {
        module: checks
        for module in SCHEDULING_MODULES
        if (ROOT / module).exists() and (checks := _type_checks(ROOT / module))
    }

    assert offenders == {}, offenders
    assert _type_checks(ROOT / DISPATCHER), "the dispatcher is where types are compared"
