"""One CORS and cache policy on ``/static/`` (#79).

Static files are public and fetched without credentials (Safari video sends
no-cors requests, see CLAUDE.md "Static file serving"), so every ``/static/``
response says the same thing, whichever origin asked and whatever the API's
own CORS policy is: ``Access-Control-Allow-Origin: *``, no
``Access-Control-Allow-Credentials``, and only the two headers a ranged media
fetch needs exposed. Only a file that was found is cached for a day.

The app is built in a subprocess with a real allowed origin, because the test
session's ``CORS_ALLOWED_ORIGINS=*`` turns credentials off and would hide the
mix this guards against.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from club_server.main import STATIC_DIR

from .app_subprocess import ProbeRequest, ProbeResponse, request_app

ALLOWED = "https://member.example.test"
DISALLOWED = "https://elsewhere.example.test"
ONE_DAY = "public, max-age=86400"


@pytest.fixture
def static_file() -> Iterator[str]:
    path = Path(STATIC_DIR) / "cors-check.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(b"static")
    yield "/static/cors-check.txt"
    path.unlink(missing_ok=True)


def _get(path: str, origin: str | None) -> ProbeRequest:
    return {
        "method": "GET",
        "path": path,
        "headers": {} if origin is None else {"Origin": origin},
    }


def _responses(*requests: ProbeRequest) -> list[ProbeResponse]:
    return request_app({"CORS_ALLOWED_ORIGINS": ALLOWED}, list(requests))


@pytest.mark.parametrize(
    "origin", [ALLOWED, DISALLOWED, None], ids=["allowed", "disallowed", "none"]
)
def test_should_send_one_cors_policy_on_a_static_file(
    static_file: str, origin: str | None
):
    (response,) = _responses(_get(static_file, origin))

    assert response["status"] == 200
    headers = response["headers"]
    assert headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in headers
    assert headers["access-control-expose-headers"] == "Content-Length, Content-Range"
    assert headers["cache-control"] == ONE_DAY


def test_should_not_cache_a_missing_static_file_for_a_day():
    (response,) = _responses(_get("/static/no-such-file.txt", DISALLOWED))

    assert response["status"] == 404
    assert response["headers"].get("cache-control") != ONE_DAY
    assert "access-control-allow-credentials" not in response["headers"]


def test_should_keep_the_api_cors_policy_for_an_allowed_origin():
    """Outside /static/ the API's own policy still applies."""
    (response,) = _responses(_get("/health", ALLOWED))

    assert response["headers"]["access-control-allow-origin"] == ALLOWED
    assert response["headers"]["access-control-allow-credentials"] == "true"
