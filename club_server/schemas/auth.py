from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from ..db.models.user import Gender
from .common import Address, CamelCaseModel


class TokenPayload(BaseModel):
    """JWT token payload for internal use."""

    sub: str
    exp: datetime
    is_super_admin: bool = False
    # Seconds since the epoch, to the millisecond; absent on tokens issued
    # before #461.
    iat: float | None = None
    # ``refresh`` on refresh tokens; access tokens carry none.
    type: str | None = None
    # The login session the token belongs to, shared by the pair issued at
    # login and every pair refreshed from it; absent before #510.
    sid: str | None = None


class LoginRequest(CamelCaseModel):
    """Login request schema."""

    username: str
    password: str


class RegisterRequest(CamelCaseModel):
    """Registration request schema."""

    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1)
    email: EmailStr | None = None
    first_name: str | None = Field(None, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=20)
    date_of_birth_utc: int | None = None
    gender: Gender | None = None
    address: Address | None = None


class TokenResponse(CamelCaseModel):
    """JWT token response schema."""

    access_token: str
    token_type: str = "bearer"
    expires_at_utc: int


class ChangePasswordRequest(CamelCaseModel):
    """Change password request schema."""

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=1)


class ResetPasswordRequest(CamelCaseModel):
    """Request password reset via email."""

    email: EmailStr


class AdminResetPasswordResponse(CamelCaseModel):
    """Response from admin password reset containing the new default password."""

    new_password: str


class RefreshTokenRequest(CamelCaseModel):
    """Refresh token request schema."""

    refresh_token: str


class RefreshTokenResponse(CamelCaseModel):
    """Refresh token response schema."""

    access_token: str
    token_type: str = "bearer"
    expires_at_utc: int
    refresh_token: str


class UsernameAvailableResponse(CamelCaseModel):
    """Response indicating whether a username is available for registration."""

    username: str
    available: bool
