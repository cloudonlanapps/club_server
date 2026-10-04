"""Unit tests for club_server.utils.get_display_name."""

from unittest.mock import MagicMock

from club_server.utils import get_display_name


def _make_user(**kwargs) -> MagicMock:
    """Create a mock User with given attributes."""
    user = MagicMock()
    user.first_name = kwargs.get("first_name")
    user.middle_name = kwargs.get("middle_name")
    user.last_name = kwargs.get("last_name")
    user.nickname = kwargs.get("nickname")
    user.use_name_publicly = kwargs.get("use_name_publicly", 0)
    user.username = kwargs.get("username", "testuser")
    return user


def test_display_name_private_with_nickname_returns_nickname():
    """When private with nickname, return nickname."""
    user = _make_user(
        use_name_publicly=0,
        nickname="Ace",
        first_name="John",
        last_name="Doe",
    )
    assert get_display_name(user) == "Ace"


def test_display_name_private_without_nickname_returns_placeholder():
    """When private without nickname, return 'Name not provided' — not the real name."""
    user = _make_user(
        use_name_publicly=0,
        nickname=None,
        first_name="John",
        last_name="Doe",
        username="johndoe",
    )
    assert get_display_name(user) == "Name not provided"


def test_display_name_public_returns_full_name():
    """When public, return full name from parts."""
    user = _make_user(
        use_name_publicly=1,
        first_name="John",
        middle_name="M",
        last_name="Doe",
    )
    assert get_display_name(user) == "John M Doe"


def test_display_name_public_no_name_falls_back_to_nickname():
    """When public with no name parts, fall back to nickname."""
    user = _make_user(
        use_name_publicly=1,
        first_name=None,
        last_name=None,
        nickname="Ace",
    )
    assert get_display_name(user) == "Ace"


def test_display_name_public_no_name_no_nickname_falls_back_to_username():
    """When public with nothing else, fall back to username."""
    user = _make_user(
        use_name_publicly=1,
        first_name=None,
        nickname=None,
        username="johndoe",
    )
    assert get_display_name(user) == "johndoe"
