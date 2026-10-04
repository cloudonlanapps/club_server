"""Every in-scope requirement has a test that names it (#385, `tdd_rules.md` §2).

Tests declare the rule they cover with ``@pytest.mark.requirement("doc:Rn")``.
This module parses the requirement documents for rule ids, scans the test
sources for the markers, and fails when an in-scope rule has none — or when a
marker names a rule no document defines, which is how a citation goes stale.

The scan is static (source text, not the collected session) so that running
a subset of the suite with ``-k`` cannot make it fail spuriously.

Scope: every rule carrying a status marker in the programme, one-off,
lifecycle, marketing, public, groups, credit, notifications, media,
users, auth, platform and evaluation documents, plus the rules in the
camp, attendance and enrollment documents that the #384 redesign changes.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
TESTS = ROOT / "tests"

FULLY_SCOPED_DOCS: dict[str, str] = {
    "programme": "programme_requirements.md",
    "oneoff": "oneoff_requirements.md",
    "lifecycle": "event_lifecycle_requirements.md",
    "marketing": "event_marketing_requirements.md",
    "public": "public_requirements.md",
    "groups": "groups_requirements.md",
    "credit": "credit_system_requirements.md",
    "notifications": "notifications_requirements.md",
    "media": "media_requirements.md",
    "users": "users_requirements.md",
    "auth": "auth_requirements.md",
    "platform": "platform_requirements.md",
    "evaluation": "evaluation_requirements.md",
}

PARTIALLY_SCOPED_DOCS: dict[str, tuple[str, frozenset[str]]] = {
    "camps": (
        "camps_requirements.md",
        frozenset(
            {"R2b", "R2c", "R5b", "R31", "R39", "R56", "R81a", "R81b", "R82a", "R105"}
        ),
    ),
    "enrollment": (
        "enrollment_requirements.md",
        frozenset({"R15", "R16", "R29a", "R29b", "R30", "R48b"}),
    ),
    "attendance": (
        "attendance_requirements.md",
        frozenset({"R7", "R7a", "R7b", "R21b", "R21c", "R21d"}),
    ),
    "venue": (
        "venue_requirements.md",
        frozenset({"R8", "R9", "R10", "R22", "R23", "R24", "R25"}),
    ),
}

_RULE_LINE = re.compile(r"^- \*\*((?:R|L)\d+[a-z]?\d?)\*\* \[(?:✅|X|GAP)[^\]]*\]")
_ANY_RULE_LINE = re.compile(r"^- \*\*((?:R|L)\d+[a-z]?\d?)\*\*")
_MARKER = re.compile(r"""requirement\(\s*["']([a-z]+:(?:R|L)\d+[a-z]?\d?)["']\s*\)""")


def rule_ids(doc: Path, *, marked_only: bool) -> set[str]:
    """Rule ids defined in ``doc``; optionally only those carrying a status."""
    pattern = _RULE_LINE if marked_only else _ANY_RULE_LINE
    found: set[str] = set()
    for line in doc.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            found.add(match.group(1))
    return found


def in_scope_rules() -> set[str]:
    """The ``doc:Rn`` ids that must each have a marker-carrying test."""
    scoped: set[str] = set()
    for prefix, filename in FULLY_SCOPED_DOCS.items():
        for rid in rule_ids(DOCS / filename, marked_only=True):
            scoped.add(f"{prefix}:{rid}")
    for prefix, (filename, wanted) in PARTIALLY_SCOPED_DOCS.items():
        defined = rule_ids(DOCS / filename, marked_only=False)
        missing = wanted - defined
        assert not missing, f"{filename} no longer defines {sorted(missing)}"
        for rid in wanted:
            scoped.add(f"{prefix}:{rid}")
    return scoped


def defined_rules() -> set[str]:
    """Every ``doc:Rn`` id any scoped document defines, marked or not."""
    defined: set[str] = set()
    for prefix, filename in FULLY_SCOPED_DOCS.items():
        defined |= {
            f"{prefix}:{r}" for r in rule_ids(DOCS / filename, marked_only=False)
        }
    for prefix, (filename, _) in PARTIALLY_SCOPED_DOCS.items():
        defined |= {
            f"{prefix}:{r}" for r in rule_ids(DOCS / filename, marked_only=False)
        }
    return defined


def covered_rules() -> set[str]:
    """Every requirement id named by a marker in the test sources."""
    covered: set[str] = set()
    for path in TESTS.glob("test_*.py"):
        if path.name == Path(__file__).name:
            continue
        covered |= set(_MARKER.findall(path.read_text(encoding="utf-8")))
    return covered


def test_should_find_rules_when_documents_are_parsed():
    """The parser sees the documents; an empty scope would pass vacuously."""
    scoped = in_scope_rules()

    assert len(scoped) > 60, sorted(scoped)
    assert "programme:R1" in scoped
    assert "oneoff:R3" in scoped
    assert "lifecycle:L12" in scoped


def test_should_have_a_marked_test_when_rule_is_in_scope():
    """§2: every in-scope rule has at least one dedicated ``test_*`` function."""
    uncovered = in_scope_rules() - covered_rules()

    assert not uncovered, (
        "requirements with no @pytest.mark.requirement test: "
        + ", ".join(sorted(uncovered))
    )


def test_should_reject_marker_when_it_names_an_undefined_rule():
    """A marker citing a rule no document defines is a stale citation."""
    unknown = covered_rules() - defined_rules()

    assert not unknown, "markers naming undefined rules: " + ", ".join(sorted(unknown))


@pytest.mark.requirement("programme:R1")
def test_should_expose_requirement_marker_when_registered(
    request: pytest.FixtureRequest,
):
    """The marker is registered, so pytest attaches it rather than warning."""
    marker = request.node.get_closest_marker("requirement")

    assert marker is not None
    assert marker.args == ("programme:R1",)
