"""Cache headers on the public surface (#297, public R20–R22).

Every ``GET`` under ``/v1/public/`` gets ``Cache-Control: public,
max-age=300`` and a strong content-derived ``ETag``; a matching
``If-None-Match`` answers 304 with the same headers. ``/health`` is
``no-store``. Media downloads live elsewhere and keep their own headers.
"""

import hashlib

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

PUBLIC_PREFIX = "/v1/public/"
PUBLIC_MAX_AGE_SECONDS = 300
HEALTH_PATH = "/health"


def _etag_for(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


class PublicCacheMiddleware(BaseHTTPMiddleware):
    """Add cache headers and conditional 304s to public reads."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        path = request.url.path
        if path == HEALTH_PATH:
            response.headers["Cache-Control"] = "no-store"
            return response
        if request.method != "GET" or not path.startswith(PUBLIC_PREFIX):
            return response
        if response.status_code != 200:
            return response

        body = b"".join([chunk async for chunk in response.body_iterator])
        etag = _etag_for(body)
        headers = dict(response.headers)
        headers["Cache-Control"] = f"public, max-age={PUBLIC_MAX_AGE_SECONDS}"
        headers["ETag"] = etag
        headers.pop("content-length", None)

        if request.headers.get("if-none-match") == etag:
            headers.pop("content-type", None)
            return Response(status_code=304, headers=headers)
        return Response(
            content=body,
            status_code=200,
            headers=headers,
            media_type=response.media_type,
        )
