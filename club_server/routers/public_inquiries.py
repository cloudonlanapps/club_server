"""Public inquiry submission and its form token (#407, public R23–R27)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db
from ..schemas.inquiry import FormTokenResponse, InquirySubmission
from ..services.inquiry import InquiryService, issue_form_token
from ..utils import get_client_ip

router = APIRouter(prefix="/public/inquiries", tags=["public"])


@router.get("/token", response_model=FormTokenResponse)
async def get_form_token() -> FormTokenResponse:
    """A fill-time token the form sends back; submissions under 3 s are dropped."""
    return FormTokenResponse(token=issue_form_token())


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_class=Response)
async def submit_inquiry(
    data: InquirySubmission,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> Response:
    """Accept a contact or interest submission; 202 with no body, always."""
    await InquiryService(db).submit(data, get_client_ip(request))
    return Response(status_code=status.HTTP_202_ACCEPTED)
