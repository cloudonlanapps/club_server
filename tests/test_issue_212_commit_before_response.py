"""Regression test for #212.

Mutating endpoints must commit their DB transaction *before* the HTTP
response is sent, otherwise a follow-up request on a different pooled
connection can fail to see the just-written row (read-your-write race).

The fix wires the session dependency (`get_db`) as a ``scope="function"``
dependency. FastAPI tears down ``function``-scoped yield dependencies on the
inner exit stack that closes *before* the response is written to the wire,
whereas the default (``request``) scope tears down *after*. This test asserts
the invariant structurally across every mounted route so a new
``Depends(get_db)`` added without the scope is caught.
"""

from fastapi.routing import APIRoute

from club_server.dependencies import get_db
from club_server.main import app


def _iter_dependants(dependant):
    yield dependant
    for sub in dependant.dependencies:
        yield from _iter_dependants(sub)


def test_get_db_is_function_scoped_on_every_route():
    offenders: list[str] = []
    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for dep in _iter_dependants(route.dependant):
            if dep.call is get_db:
                checked += 1
                if dep.scope != "function":
                    offenders.append(
                        f"{route.path} [{','.join(sorted(route.methods))}] scope={dep.scope!r}"
                    )

    assert checked > 0, "expected at least one route to depend on get_db"
    assert not offenders, (
        "get_db must be declared with scope='function' so the session commit "
        "runs before the response is sent (#212). Offending routes:\n"
        + "\n".join(offenders)
    )
