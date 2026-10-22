"""Cache headers on media downloads (media R92–R94).

Media anonymous callers may view can be kept by any cache for a day.
Media restricted by its access roles is ``private, no-cache``: the browser
must ask the server before every reuse, so the access check runs for
whoever is signed in at the time (#66). The answer to that question is a
bodiless 304 when the browser's copy is current. Encrypted media is never
stored.
"""

import os
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, Response

PUBLIC_DOWNLOAD_CACHE = "public, max-age=86400"
PRIVATE_DOWNLOAD_CACHE = "private, no-cache"
ENCRYPTED_DOWNLOAD_CACHE = "private, no-store"

DOWNLOAD_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
}


def _matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    tags = [tag.strip().removeprefix("W/") for tag in if_none_match.split(",")]
    return "*" in tags or etag in tags


def plain_download_response(
    request: Request, file_path: Path, media_type: str, cache_control: str
) -> Response:
    """The file, or a 304 when ``If-None-Match`` names its current tag.

    Call only after the access check: a 304 tells the caller its copy is
    still good to show.
    """
    headers = {**DOWNLOAD_CORS_HEADERS, "Cache-Control": cache_control}
    response = FileResponse(
        path=str(file_path),
        media_type=media_type,
        headers=headers,
        stat_result=os.stat(file_path),
    )
    etag = response.headers["etag"]
    if _matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={**headers, "ETag": etag})
    return response
