"""Password authentication, one-time account tokens, and JWT authorization."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pwdlib import PasswordHash

from app.config import JWTSettings
from app.database import get_db
from app.models import User
from app.schemas import UserCreate, UserLogin
from app.user_cache import cache_user, get_cached_user


password_hash = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    """Build the consistent 401 response used for invalid credentials and tokens."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials or token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def hash_password(password: str) -> str:
    """Hash a plaintext password with the configured password-hashing scheme."""
    return password_hash.hash(password)


def register_user(db: Session, payload: UserCreate) -> User:
    """Create a normalized user account, reporting duplicate addresses as 409."""
    email = str(payload.email).lower()
    existing_user = db.scalar(select(User).where(User.email == email))
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    user = User(
        email=email,
        hashed_password=hash_password(payload.password.get_secret_value()),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email already exists.",
            ) from exc
        raise

    db.refresh(user)
    return user


def authenticate_user(db: Session, payload: UserLogin) -> User:
    """Authenticate a user and require email verification before granting access."""
    email = str(payload.email).lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not password_hash.verify(
        payload.password.get_secret_value(),
        user.hashed_password,
    ):
        raise _unauthorized()
    if not user.is_email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email address must be verified before login.",
        )
    return user


def issue_email_verification_token(db: Session, user: User) -> str:
    """Issue and persist a single-use email-verification token for 24 hours."""
    token = secrets.token_urlsafe(32)
    user.email_verification_token_hash = hashlib.sha256(token.encode()).hexdigest()
    user.email_verification_expires_at = datetime.now(timezone.utc) + timedelta(
        hours=24
    )
    db.commit()
    db.refresh(user)
    return token


def verify_email_token(db: Session, token: str) -> User:
    """Verify an account with a valid token and consume that token."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = db.scalar(
        select(User).where(User.email_verification_token_hash == token_hash)
    )
    if user is None or _is_expired(user.email_verification_expires_at):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token is invalid or expired.",
        )
    user.is_email_verified = True
    user.email_verification_token_hash = None
    user.email_verification_expires_at = None
    db.commit()
    db.refresh(user)
    return user


def issue_password_reset_token(db: Session, user: User) -> str:
    """Issue and persist a single-use password-reset token for one hour."""
    token = secrets.token_urlsafe(32)
    user.password_reset_token_hash = hashlib.sha256(token.encode()).hexdigest()
    user.password_reset_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()
    return token


def reset_user_password(db: Session, token: str, new_password: str) -> User:
    """Replace a password using a valid reset token and consume the token."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = db.scalar(select(User).where(User.password_reset_token_hash == token_hash))
    if user is None or _is_expired(user.password_reset_expires_at):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset token is invalid or expired.",
        )
    user.hashed_password = hash_password(new_password)
    user.password_reset_token_hash = None
    user.password_reset_expires_at = None
    db.commit()
    db.refresh(user)
    return user


def _is_expired(expires_at: datetime | None) -> bool:
    """Return whether an expiry timestamp is absent or no longer in the future."""
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def create_token(user_id: int, token_type: str, lifetime: timedelta) -> str:
    """Create a signed JWT for a user, token purpose, and lifetime."""
    settings = JWTSettings.from_env()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "token_type": token_type,
            "iat": now,
            "exp": now + lifetime,
        },
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def create_token_pair(user_id: int) -> dict[str, str | int]:
    """Create an access/refresh token pair and return their expiry durations."""
    settings = JWTSettings.from_env()
    access_lifetime = timedelta(minutes=settings.access_token_expire_minutes)
    refresh_lifetime = timedelta(days=settings.refresh_token_expire_days)
    return {
        "access_token": create_token(user_id, "access", access_lifetime),
        "refresh_token": create_token(user_id, "refresh", refresh_lifetime),
        "token_type": "bearer",
        "expires_in": int(access_lifetime.total_seconds()),
        "refresh_expires_in": int(refresh_lifetime.total_seconds()),
    }


def decode_token(token: str, expected_type: str) -> int:
    """Validate a JWT's required claims and return its positive user ID."""
    settings = JWTSettings.from_env()
    try:
        claims = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            options={"require": ["exp", "sub", "token_type"]},
        )
        if claims.get("token_type") != expected_type:
            raise _unauthorized()
        user_id = int(claims["sub"])
        if user_id <= 0:
            raise _unauthorized()
    except (InvalidTokenError, TypeError, ValueError) as exc:
        raise _unauthorized() from exc
    return user_id


def get_user_from_token(db: Session, token: str, expected_type: str) -> User:
    """Resolve a valid typed token to its registered user."""
    user_id = decode_token(token, expected_type)
    user = db.get(User, user_id)
    if user is None:
        raise _unauthorized()
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the bearer access token, consulting Redis before the database."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    user_id = decode_token(credentials.credentials, "access")
    user = get_cached_user(user_id)
    if user is not None:
        return user
    user = db.get(User, user_id)
    if user is None:
        raise _unauthorized()
    cache_user(user)
    return user


def create_access_token_for_refresh(
    db: Session,
    refresh_token: str,
) -> dict[str, str | int]:
    """Exchange a valid refresh token for a new access token."""
    user = get_user_from_token(db, refresh_token, "refresh")
    settings = JWTSettings.from_env()
    access_lifetime = timedelta(minutes=settings.access_token_expire_minutes)
    return {
        "access_token": create_token(user.id, "access", access_lifetime),
        "token_type": "bearer",
        "expires_in": int(access_lifetime.total_seconds()),
    }
