from typing import ClassVar

from pydantic import ConfigDict

from ..db.models.group_join_request import GroupJoinRequest
from .common import CamelCaseModel


class JoinRequestCreate(CamelCaseModel):
    """Body for creating a join request."""

    reason: str | None = None


class JoinRequestRejectBody(CamelCaseModel):
    """Body for rejecting a join request."""

    reason: str | None = None


class JoinRequestResponse(CamelCaseModel):
    """Wire shape for a single join-request row."""

    id: int
    group_id: int
    group_name: str
    username: str
    status: str
    requested_at: int
    decided_at: int | None = None
    decided_by: str | None = None
    reason: str | None = None

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )

    @classmethod
    def from_model(cls, req: GroupJoinRequest) -> "JoinRequestResponse":
        return cls(
            id=req.id,
            group_id=req.group_id,
            group_name=req.group.name,
            username=req.username,
            status=req.status,
            requested_at=req.requested_at,
            decided_at=req.decided_at,
            decided_by=req.decided_by,
            reason=req.reason,
        )
