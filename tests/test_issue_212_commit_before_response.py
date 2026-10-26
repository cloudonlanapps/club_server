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

from collections.abc import Iterator

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

from club_server.dependencies import get_db
from club_server.main import app


def _api_routes(routes: list[BaseRoute]) -> Iterator[APIRoute]:
    """Every APIRoute, including those inside included routers.

    From FastAPI 0.156 ``include_router`` adds one lazy wrapper per router to
    ``app.routes`` instead of copying its routes, and the wrapper keeps the
    router as ``original_router`` (#88).
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _api_routes(inner.routes)


def _iter_dependants(dependant: Dependant) -> Iterator[Dependant]:
    yield dependant
    for sub in dependant.dependencies:
        yield from _iter_dependants(sub)


def test_get_db_is_function_scoped_on_every_route():
    offenders: list[str] = []
    checked = 0
    routes = list(_api_routes(app.routes))
    for route in routes:
        for dep in _iter_dependants(route.dependant):
            if dep.call is get_db:
                checked += 1
                if dep.scope != "function":
                    offenders.append(
                        f"{route.path} [{','.join(sorted(route.methods or set[str]()))}] scope={dep.scope!r}"
                    )

    # The walk must reach every operation the app publishes, or the guard
    # would pass while checking only some of them.
    published = sum(
        len([m for m in ops if m in {"get", "post", "put", "patch", "delete", "head"}])
        for ops in app.openapi()["paths"].values()
    )
    assert (
        sum(len(r.methods or set[str]()) for r in routes if r.include_in_schema)
        == published
    )
    assert checked > 0, "expected at least one route to depend on get_db"
    assert not offenders, (
        "get_db must be declared with scope='function' so the session commit "
        "runs before the response is sent (#212). Offending routes:\n"
        + "\n".join(offenders)
    )
