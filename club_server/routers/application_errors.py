"""HTTP answers for the application routers' domain errors (#93).

A router catches ``DOMAIN_ERRORS`` and raises ``to_http(exc)``, so every
application route answers the same code for the same failure.
"""

from fastapi import HTTPException, status

from ..exceptions import (
    ApplicationDoesNotFitException,
    ApplicationIncompleteException,
    ApplicationNotEligibleException,
    ApplicationNotFoundException,
    ApplicationNotPublishedException,
    ApplicationReferenceException,
    ApplicationTransitionException,
    IdentityDocumentRequiredException,
    InvalidStateException,
    UserNotFoundException,
)

DOMAIN_ERRORS = (
    ApplicationDoesNotFitException,
    ApplicationIncompleteException,
    ApplicationNotEligibleException,
    ApplicationNotFoundException,
    ApplicationNotPublishedException,
    ApplicationReferenceException,
    ApplicationTransitionException,
    IdentityDocumentRequiredException,
    InvalidStateException,
    UserNotFoundException,
)

_NOT_FOUND: dict[type[Exception], str] = {
    ApplicationNotFoundException: "APPLICATION_NOT_FOUND",
    UserNotFoundException: "USER_NOT_FOUND",
}

_UNPROCESSABLE: dict[type[Exception], str] = {
    ApplicationNotPublishedException: "APPLICATION_NOT_PUBLISHED",
    ApplicationNotEligibleException: "APPLICATION_NOT_ELIGIBLE",
    ApplicationReferenceException: "APPLICATION_INVALID_REFERENCE",
    ApplicationTransitionException: "INVALID_TRANSITION",
    IdentityDocumentRequiredException: "IDENTITY_DOCUMENT_REQUIRED",
    InvalidStateException: "INVALID_STATE",
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
    if isinstance(exc, ApplicationIncompleteException):
        return _http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "APPLICATION_INCOMPLETE",
            str(exc),
            {"missing": exc.missing, "language": exc.language},
        )
    if isinstance(exc, ApplicationDoesNotFitException):
        return _http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "APPLICATION_DOES_NOT_FIT",
            str(exc),
            {"overflowPt": exc.overflow},
        )
    for kind, error in _NOT_FOUND.items():
        if isinstance(exc, kind):
            return _http(status.HTTP_404_NOT_FOUND, error, str(exc))
    for kind, error in _UNPROCESSABLE.items():
        if isinstance(exc, kind):
            return _http(status.HTTP_422_UNPROCESSABLE_CONTENT, error, str(exc))
    raise exc
