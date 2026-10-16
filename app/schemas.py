"""Pydantic request and response schemas for the Contacts API."""

from datetime import date

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, model_validator


class UserCredentials(BaseModel):
    """Email and secret password fields shared by registration and login."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: EmailStr
    password: SecretStr = Field(min_length=8, max_length=128)


class UserCreate(UserCredentials):
    """Validated credentials used to register an account."""

    pass


class UserLogin(UserCredentials):
    """Validated credentials used to authenticate an account."""

    pass


class UserRead(BaseModel):
    """Public account fields returned by API endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    avatar_url: str | None = None


class EmailTokenRequest(BaseModel):
    """Request body carrying an email-verification token."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=256)


class EmailAddressRequest(BaseModel):
    """Request body carrying an email address."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Request body for consuming a reset token and setting a password."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=256)
    new_password: SecretStr = Field(min_length=8, max_length=128)


class RefreshTokenRequest(BaseModel):
    """Request body carrying a refresh JWT."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1)


class TokenPair(BaseModel):
    """Access and refresh JWTs with their advertised lifetimes."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_expires_in: int


class AccessTokenResponse(BaseModel):
    """Response containing a new access JWT."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class ContactCreate(BaseModel):
    """Validated contact fields accepted when creating a contact."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: str = Field(min_length=1, max_length=50)
    birthday: date
    extra_data: str | None = None


class ContactUpdate(BaseModel):
    """Partial contact update; required fields may be omitted, not nulled."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, min_length=1, max_length=50)
    birthday: date | None = None
    extra_data: str | None = None

    @model_validator(mode="after")
    def reject_null_required_fields(self):
        """Reject explicit nulls for fields that are required on a contact."""
        required_fields = {"first_name", "last_name", "email", "phone", "birthday"}
        for field in required_fields & self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ContactRead(BaseModel):
    """Public contact fields returned by API endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    email: EmailStr
    phone: str
    birthday: date
    extra_data: str | None
