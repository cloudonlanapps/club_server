from typing import ClassVar

from pydantic import ConfigDict, Field, ValidationInfo, model_validator

from ..utils import now_utc_ms
from .common import CamelCaseModel


class CreditAccountResponse(CamelCaseModel):
    """One credit account as seen by an admin or its owner."""

    account_id: str
    membername: str
    kind: str
    event_id: int | None
    is_trial: bool
    balance: int
    valid_from_utc: int
    valid_until_utc: int
    usable: bool
    state: str
    opened_at_utc: int
    opened_by: str | None
    closed_at_utc: int | None

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)


class CreditEntryResponse(CamelCaseModel):
    """One movement of credit on the ledger."""

    id: int
    account_id: str
    membername: str
    amount: int
    entry_type: str
    event_id: int | None
    occurrence_time_utc: int | None
    reason: str
    actor_username: str | None
    created_at_utc: int
    offsets_entry_id: int | None
    balance_after: int
    """The account's balance after this entry (#449)."""
    total_after: int
    """The member's credit across all their accounts after this entry;
    transfers between their own accounts leave it unchanged (#449)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)


class MemberCreditStatusResponse(CamelCaseModel):
    """A member's standing on one programme: can they be marked today?"""

    membername: str
    usable_credits: int
    bound_credits: int
    paying_account_id: str | None
    blocked: bool
    next_expiry_utc: int | None


class OpenAccountRequest(CamelCaseModel):
    """Open a credit account for a member (R18-R24)."""

    membername: str
    credits: int
    valid_from_utc: int
    valid_until_utc: int
    reason: str = Field(..., min_length=1)
    event_id: int | None = None
    is_trial: bool = False


class ExtendValidityRequest(CamelCaseModel):
    """Move an account's validity end forwards (R62)."""

    valid_until_utc: int
    reason: str = Field(..., min_length=1)


class ReverseGrantRequest(CamelCaseModel):
    """Undo an admin's own mistaken grant (R58, R59).

    ``credits`` omitted reverses the whole remaining balance.
    """

    reason: str = Field(..., min_length=1)
    credits: int | None = None


class TransferRequest(CamelCaseModel):
    """Close an account, moving what survives the penalty into a new one.

    Serves expiry disposition, voluntary drop-out and admin removal alike
    (R63, R65-R69, R75) — they differ only in the penalty.
    """

    penalty: int = Field(..., ge=0)
    valid_from_utc: int
    valid_until_utc: int
    reason: str = Field(..., min_length=1)


STORED_DISPOSITION = "storedDisposition"
"""Validation-context key marking a disposition read back from storage."""


class CreditDispositionRequest(CamelCaseModel):
    """What happens to a departing member's programme credit (R71, R73).

    Carries the same fields as a transfer, because it is the same operation:
    the balance moves into a new general account after a flat penalty, and
    nothing survives a penalty that consumes it.
    """

    penalty: int = Field(..., ge=0)
    valid_from_utc: int
    valid_until_utc: int
    reason: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _window_is_open(self, info: ValidationInfo) -> "CreditDispositionRequest":
        """The window must end after it starts and not have closed yet (#476).

        Checked where the admin states it, so a bad window is a 422 rather
        than a 500 on the immediate path or a failing row in every hourly
        sweep on the deferred one. A disposition read back from storage
        (context ``{STORED_DISPOSITION: True}``) skips the clock check: it
        was validated when it was stated.
        """
        if self.valid_until_utc <= self.valid_from_utc:
            raise ValueError("validUntilUtc must be after validFromUtc")
        stored = bool(info.context and info.context.get(STORED_DISPOSITION))
        if not stored and self.valid_until_utc <= now_utc_ms():
            raise ValueError("validUntilUtc has already passed")
        return self


class TransferResponse(CamelCaseModel):
    """The drained account and the one the transfer created, if any."""

    source: CreditAccountResponse
    created: CreditAccountResponse | None
