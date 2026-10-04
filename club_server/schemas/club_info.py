"""Public club-info document (#296)."""

from typing import Any

from .common import CamelCaseModel
from .media import MediaRef


class PublicClubInfoResponse(CamelCaseModel):
    """The club's public identity and the site's media slots.

    A slot carries the media descriptor rather than a bare uuid (#424): the
    landing hero is a video on this site and a still on the next, and a client
    handed a uuid has no way to tell which.
    """

    club_info: dict[str, Any]
    site_media: dict[str, MediaRef]
