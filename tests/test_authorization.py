"""Unit tests for the organizer-or-admin authorization helpers (#158)."""

import json

import pytest
from fastapi import HTTPException

from club_server.db.models.user import User
from club_server.dependencies import (
    check_organizer_or_admin,
    require_organizer_or_admin,
)


def _user(
    username: str,
    *,
    roles: list[str] | None = None,
    is_super_admin: bool = False,
) -> User:
    return User(
        username=username,
        password="x",
        first_name=username,
        roles=json.dumps({"roles": roles or []}),
        is_super_admin=1 if is_super_admin else 0,
        created_at=0,
    )


# --- check_organizer_or_admin (occurrences helper) ---


def test_super_admin_is_authorized_regardless_of_organizer():
    user = _user("anyone", is_super_admin=True)
    assert check_organizer_or_admin("someone_else", user) is True
    assert check_organizer_or_admin(None, user) is True


def test_admin_role_is_authorized_regardless_of_organizer():
    user = _user("admin_user", roles=["admin"])
    assert check_organizer_or_admin("someone_else", user) is True


def test_organizer_coach_is_authorized():
    user = _user("organizer_coach", roles=["coach"])
    assert check_organizer_or_admin("organizer_coach", user) is True


def test_non_organizer_coach_is_rejected():
    user = _user("other_coach", roles=["coach"])
    assert check_organizer_or_admin("organizer_coach", user) is False


def test_roleless_user_is_rejected():
    user = _user("alice", roles=[])
    assert check_organizer_or_admin("organizer_coach", user) is False


def test_event_with_no_organizer_rejects_non_admin():
    user = _user("other_coach", roles=["coach"])
    assert check_organizer_or_admin(None, user) is False


# --- require_organizer_or_admin (raises 403) ---


def test_require_passes_for_admin():
    require_organizer_or_admin("someone_else", _user("a", roles=["admin"]))


def test_require_passes_for_organizer():
    require_organizer_or_admin(
        "organizer_coach", _user("organizer_coach", roles=["coach"])
    )


def test_require_raises_403_for_non_organizer_coach():
    with pytest.raises(HTTPException) as exc_info:
        require_organizer_or_admin(
            "organizer_coach", _user("other_coach", roles=["coach"])
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "INSUFFICIENT_PERMISSION"


def test_require_raises_403_for_roleless_user():
    with pytest.raises(HTTPException) as exc_info:
        require_organizer_or_admin("organizer_coach", _user("alice", roles=[]))
    assert exc_info.value.status_code == 403
