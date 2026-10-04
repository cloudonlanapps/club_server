"""What this deployment can do (#339).

Public (#443). The signup page runs before login and needs to know whether
identity verification is on, to tell an applicant whether they will be asked
for a document. Publishing the document reveals nothing new: each flag is
already discoverable anonymously — a disabled module's routes answer 503
before authentication, public marketing is public by design, and register
answers ``registered`` or ``pending``. The price is that a capability which
must stay private cannot join this document.
"""

from fastapi import APIRouter

from ..config import settings
from ..schemas.common import CapabilitiesResponse

router = APIRouter(prefix="/capabilities", tags=["Capabilities"])


@router.get("", response_model=CapabilitiesResponse)
async def get_capabilities() -> CapabilitiesResponse:
    """Report what this deployment can do.

    A client cannot learn this from ``openapi.json``: the credit endpoints
    are registered on every deployment so the published schema does not
    vary with configuration (#294, R94). Without this it would have to call
    a credit endpoint and interpret a 503, which makes an error response
    part of the happy path.

    Describes the deployment, not the caller — every client, signed in or
    not, gets the same document. Read-only; these are deploy configuration
    and not settable at runtime.
    """
    return CapabilitiesResponse(
        credit_system=settings.credit_system_enabled,
        evaluations=settings.evaluations_enabled,
        event_marketing=settings.event_marketing_enabled,
        identity_verification=settings.identity_verification_required,
    )
