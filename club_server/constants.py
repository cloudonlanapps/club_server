"""Cross-module constants (coding rule 18: no magic values).

Media link tags name the role a media item plays for its owner. The client
(``club_client``) defines the same strings; they are part of the API contract.
"""

USER_AVATAR_TAG = "user_avatar"
VENUE_IMAGE_TAG = "venue_image"
EVENT_COVER_TAG = "event_cover"
EVENT_GALLERY_TAG = "event_gallery"
