"""The server reports the VERSION file as its version, and nothing else."""

import importlib.metadata
from pathlib import Path

import pytest
from httpx import AsyncClient

VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()


@pytest.mark.asyncio
async def test_should_report_the_version_file_at_root_and_in_openapi(
    client: AsyncClient,
):
    root = await client.get("/")
    assert root.status_code == 200
    assert root.json()["version"] == VERSION

    schema = await client.get("/openapi.json")
    assert schema.json()["info"]["version"] == VERSION


def test_should_take_the_package_version_from_the_version_file():
    assert importlib.metadata.version("club-server") == VERSION
