"""Shared HTTP mappings for the evaluation routers (#302, #535).

Kept in one place so the staff, member, template and lifecycle surfaces
answer with the same codes for the same domain failures. A router catches
``DOMAIN_ERRORS`` and raises ``to_http(exc)``.
"""

from fastapi import HTTPException, status

from ..exceptions import (
    EvaluationAnswerInvalidException,
    EvaluationDuplicateException,
    EvaluationEvidenceInvalidException,
    EvaluationIncompleteException,
    EvaluationItemNotFoundException,
    EvaluationItemTypeFixedException,
    EvaluationLayoutInvalidException,
    EvaluationNotEligibleException,
    EvaluationNotFoundException,
    EvaluationOriginMismatchException,
    EvaluationPeriodInFutureException,
    EvaluationTemplateInUseException,
    EvaluationTemplateNameTakenException,
    EvaluationTemplateNotFoundException,
    EvaluationTransitionException,
    EventNotFoundException,
    InvalidStateException,
    UserNotFoundException,
)

DOMAIN_ERRORS = (
    EvaluationAnswerInvalidException,
    EvaluationDuplicateException,
    EvaluationEvidenceInvalidException,
    EvaluationIncompleteException,
    EvaluationItemNotFoundException,
    EvaluationItemTypeFixedException,
    EvaluationLayoutInvalidException,
    EvaluationNotEligibleException,
    EvaluationNotFoundException,
    EvaluationOriginMismatchException,
    EvaluationPeriodInFutureException,
    EvaluationTemplateInUseException,
    EvaluationTemplateNameTakenException,
    EvaluationTemplateNotFoundException,
    EvaluationTransitionException,
    EventNotFoundException,
    InvalidStateException,
    UserNotFoundException,
)

_UNPROCESSABLE: dict[type[Exception], str] = {
    EvaluationAnswerInvalidException: "INVALID_ANSWER",
    EvaluationDuplicateException: "DUPLICATE_EVALUATION",
    EvaluationEvidenceInvalidException: "INVALID_EVIDENCE",
    EvaluationItemTypeFixedException: "ITEM_TYPE_FIXED",
    EvaluationLayoutInvalidException: "INVALID_LAYOUT",
    EvaluationNotEligibleException: "NOT_ELIGIBLE",
    EvaluationOriginMismatchException: "ORIGIN_MISMATCH",
    EvaluationPeriodInFutureException: "PERIOD_IN_FUTURE",
    EvaluationTemplateNameTakenException: "TEMPLATE_NAME_TAKEN",
    EvaluationTransitionException: "INVALID_TRANSITION",
    InvalidStateException: "INVALID_STATE",
}

_NOT_FOUND: dict[type[Exception], str] = {
    EvaluationItemNotFoundException: "ITEM_NOT_FOUND",
    EvaluationNotFoundException: "EVALUATION_NOT_FOUND",
    EvaluationTemplateNotFoundException: "TEMPLATE_NOT_FOUND",
    EventNotFoundException: "EVENT_NOT_FOUND",
    UserNotFoundException: "USER_NOT_FOUND",
}


def _http(
    code: int, error: str, message: str, details: dict[str, object] | None = None
) -> HTTPException:
    detail: dict[str, object] = {"code": error, "message": message}
    if details is not None:
        detail["details"] = details
    return HTTPException(status_code=code, detail=detail)


def to_http(exc: Exception) -> HTTPException:
    """The HTTP answer for one of ``DOMAIN_ERRORS``."""
    if isinstance(exc, EvaluationIncompleteException):
        return _http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "INCOMPLETE",
            str(exc),
            {"itemIds": exc.item_ids},
        )
    if isinstance(exc, EvaluationTemplateInUseException):
        return _http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "TEMPLATE_IN_USE",
            str(exc),
            {"count": exc.count},
        )
    for kind, error in _NOT_FOUND.items():
        if isinstance(exc, kind):
            return _http(status.HTTP_404_NOT_FOUND, error, str(exc))
    for kind, error in _UNPROCESSABLE.items():
        if isinstance(exc, kind):
            return _http(status.HTTP_422_UNPROCESSABLE_CONTENT, error, str(exc))
    raise exc


def evaluation_not_found(evaluation_id: int) -> HTTPException:
    """404 for an evaluation that is absent, soft-deleted, or not the caller's."""
    return to_http(EvaluationNotFoundException(evaluation_id))
