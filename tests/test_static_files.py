"""Static files are served by the generic ``/static`` mount alone (#438).

The coach-image route that answered a missing file with a placeholder JPEG
dates from when coach photos were static files; they go through the media
API now, and the route is gone. What remains is the plain mount: a file that
exists is served, and one that does not is 404.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from httpx import AsyncClient

from club_server.main import STATIC_DIR, app

COACHES = STATIC_DIR / "images" / "coaches"


@pytest.fixture
def coach_images() -> Iterator[Path]:
    """A coaches directory holding the old placeholder and one real photo."""
    COACHES.mkdir(parents=True, exist_ok=True)
    written = [COACHES / "coach-placeholder.jpg", COACHES / "ann.jpg"]
    _ = written[0].write_bytes(b"placeholder")
    _ = written[1].write_bytes(b"ann's photo")
    yield COACHES
    for path in written:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_should_return_404_for_missing_coach_image_when_a_placeholder_exists(
    client: AsyncClient, coach_images: Path
):
    response = await client.get("/static/images/coaches/nobody.jpg")

    assert response.status_code == 404
    assert response.content != b"placeholder"


@pytest.mark.asyncio
async def test_should_serve_an_existing_file_under_coaches_through_the_static_mount(
    client: AsyncClient, coach_images: Path
):
    response = await client.get("/static/images/coaches/ann.jpg")

    assert response.status_code == 200
    assert response.content == b"ann's photo"


def test_should_not_publish_a_coach_image_route_in_the_schema():
    paths = app.openapi()["paths"]

    assert not [p for p in paths if p.startswith("/static")], sorted(paths)
