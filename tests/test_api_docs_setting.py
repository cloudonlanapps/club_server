"""The API docs are served only when API_DOCS_ENABLED is set (#78).

Swagger UI (/docs), ReDoc (/redoc) and the schema (/openapi.json) map every
route, admin ones included. A deployment that says nothing serves none of
them; the dev and test stacks turn them on. The deploy tooling writes an unset
key as an empty value, so empty reads as off.

The app is built in a subprocess for each value, because the test session runs
with the docs on (tests/conftest.py) and builds its app once.
"""

import pytest

from .app_subprocess import ProbeRequest, request_app

DOC_PATHS = ["/docs", "/redoc", "/openapi.json"]


def _statuses(value: str | None) -> list[int]:
    requests: list[ProbeRequest] = [
        {"method": "GET", "path": path, "headers": {}} for path in DOC_PATHS
    ]
    return [r["status"] for r in request_app({"API_DOCS_ENABLED": value}, requests)]


@pytest.mark.parametrize("value", [None, "", "false"], ids=["unset", "empty", "false"])
def test_should_not_serve_the_api_docs_unless_enabled(value: str | None):
    assert _statuses(value) == [404, 404, 404]


def test_should_serve_the_api_docs_when_enabled():
    assert _statuses("true") == [200, 200, 200]
