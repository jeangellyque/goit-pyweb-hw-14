"""FastAPI routes for account management, avatars, and contact operations."""

import logging
import smtplib
from datetime import date, timedelta

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import (
    authenticate_user,
    create_access_token_for_refresh,
    create_token_pair,
    get_current_user,
    issue_email_verification_token,
    issue_password_reset_token,
    register_user,
    reset_user_password,
    verify_email_token,
)
from app.cloudinary_service import InvalidAvatarError, upload_avatar
from app.config import CORSSettings
from app.database import get_db
from app.mailer import send_password_reset_email, send_verification_email
from app.models import Contact, User
from app.rate_limit import limit_contact_creation, limit_contact_requests
from app.schemas import (
    AccessTokenResponse,
    ContactCreate,
    ContactRead,
    ContactUpdate,
    EmailAddressRequest,
    EmailTokenRequest,
    PasswordResetConfirm,
    RefreshTokenRequest,
    TokenPair,
    UserCreate,
    UserLogin,
    UserRead,
)
from app.user_cache import invalidate_user_cache


logger = logging.getLogger(__name__)


app = FastAPI(
    title="Contacts API",
    description="A REST API for managing contacts.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CORSSettings.from_env().allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post(
    "/auth/register",
    response_model=UserRead,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    """Register a user and email a verification link."""
    user = register_user(db, payload)
    token = issue_email_verification_token(db, user)
    try:
        send_verification_email(user.email, token)
    except (OSError, smtplib.SMTPException) as exc:
        logger.exception("Unable to send email verification message.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email delivery is temporarily unavailable.",
        ) from exc
    return user


@app.post("/auth/login", response_model=TokenPair)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    """Validate credentials and return a pair of bearer tokens."""
    user = authenticate_user(db, payload)
    return create_token_pair(user.id)


@app.post("/auth/refresh", response_model=AccessTokenResponse)
def refresh_access_token(
    payload: RefreshTokenRequest,
    db: Session = Depends(get_db),
):
    """Exchange a refresh token for a short-lived access token."""
    return create_access_token_for_refresh(db, payload.refresh_token)


@app.post("/auth/verify-email", response_model=UserRead)
def verify_email(payload: EmailTokenRequest, db: Session = Depends(get_db)):
    """Verify an email address using a token in the request body."""
    user = verify_email_token(db, payload.token)
    invalidate_user_cache(user.id)
    return user


@app.get("/auth/verify-email", response_model=UserRead)
def verify_email_link(
    token: str = Query(min_length=20, max_length=256),
    db: Session = Depends(get_db),
):
    """Verify an email address using a token from a link query parameter."""
    user = verify_email_token(db, token)
    invalidate_user_cache(user.id)
    return user


@app.post("/auth/resend-verification-email")
def resend_verification_email(
    payload: EmailAddressRequest,
    db: Session = Depends(get_db),
):
    """Resend verification instructions without disclosing account existence."""
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    message = {"detail": "If the account needs verification, an email has been sent."}
    if user is not None and not user.is_email_verified:
        token = issue_email_verification_token(db, user)
        try:
            send_verification_email(user.email, token)
        except (OSError, smtplib.SMTPException) as exc:
            logger.exception("Unable to resend email verification message.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Email delivery is temporarily unavailable.",
            ) from exc
    return message


@app.post("/auth/request-password-reset")
def request_password_reset(
    payload: EmailAddressRequest,
    db: Session = Depends(get_db),
):
    """Send reset instructions without disclosing account existence."""
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    message = {"detail": "If an account exists, a password reset email has been sent."}
    if user is not None:
        token = issue_password_reset_token(db, user)
        try:
            send_password_reset_email(user.email, token)
        except (OSError, smtplib.SMTPException) as exc:
            logger.exception("Unable to send password reset message.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Email delivery is temporarily unavailable.",
            ) from exc
    return message


@app.post("/auth/reset-password")
def confirm_password_reset(
    payload: PasswordResetConfirm,
    db: Session = Depends(get_db),
):
    """Consume a password-reset token and invalidate the user's cache."""
    user = reset_user_password(
        db,
        payload.token,
        payload.new_password.get_secret_value(),
    )
    invalidate_user_cache(user.id)
    return {"detail": "Password has been reset."}


@app.get("/auth/me", response_model=UserRead)
def read_current_user(current_user: User = Depends(get_current_user)):
    """Return the authenticated user's public profile."""
    return current_user


@app.post("/users/avatar", response_model=UserRead)
def update_avatar(
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Validate and upload an avatar, then persist its secure URL."""
    try:
        avatar_url = upload_avatar(image.file, image.content_type)
    except InvalidAvatarError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    user = db.get(User, current_user.id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials or token.",
        )
    user.avatar_url = avatar_url
    db.commit()
    db.refresh(user)
    invalidate_user_cache(user.id)
    return user


def upcoming_birthday_date(birthday: date, today: date, days: int = 7) -> date | None:
    """Return the birthday occurrence in ``[today, today + days)``, if any."""
    for offset in range(days):
        occurrence = today + timedelta(days=offset)
        if (occurrence.month, occurrence.day) == (birthday.month, birthday.day):
            return occurrence
    return None


def commit_contact(db: Session, contact: Contact) -> Contact:
    """Commit and refresh a contact, mapping duplicate emails to HTTP 409."""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A contact with this email already exists.",
        ) from exc
    db.refresh(contact)
    return contact


@app.post(
    "/contacts",
    response_model=ContactRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(limit_contact_requests),
        Depends(limit_contact_creation),
    ],
)
def create_contact(
    payload: ContactCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a contact owned by the authenticated user."""
    contact = Contact(**payload.model_dump(), owner_id=current_user.id)
    db.add(contact)
    return commit_contact(db, contact)


@app.get(
    "/contacts",
    response_model=list[ContactRead],
    dependencies=[Depends(limit_contact_requests)],
)
def list_contacts(
    search: str | None = Query(default=None, min_length=1),
    first_name: str | None = Query(default=None, min_length=1),
    last_name: str | None = Query(default=None, min_length=1),
    email: str | None = Query(default=None, min_length=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List the current user's contacts with optional case-insensitive filters."""
    statement = select(Contact).where(Contact.owner_id == current_user.id)
    if search:
        pattern = f"%{search}%"
        statement = statement.where(
            or_(
                Contact.first_name.ilike(pattern),
                Contact.last_name.ilike(pattern),
                Contact.email.ilike(pattern),
            )
        )
    for field, value in (
        (Contact.first_name, first_name),
        (Contact.last_name, last_name),
        (Contact.email, email),
    ):
        if value:
            statement = statement.where(field.ilike(f"%{value}%"))
    return db.scalars(statement.order_by(Contact.id)).all()


@app.get(
    "/contacts/birthdays",
    response_model=list[ContactRead],
    dependencies=[Depends(limit_contact_requests)],
)
def list_upcoming_birthdays(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List owned contacts with birthdays in the next seven days."""
    today = date.today()
    upcoming = []
    for contact in db.scalars(
        select(Contact).where(Contact.owner_id == current_user.id)
    ):
        occurrence = upcoming_birthday_date(contact.birthday, today)
        if occurrence is not None:
            upcoming.append((occurrence, contact))
    upcoming.sort(key=lambda item: (item[0], item[1].first_name, item[1].last_name))
    return [contact for _, contact in upcoming]


@app.get(
    "/contacts/{contact_id}",
    response_model=ContactRead,
    dependencies=[Depends(limit_contact_requests)],
)
def get_contact(
    contact_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return a contact only if it belongs to the authenticated user."""
    contact = db.scalar(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.owner_id == current_user.id,
        )
    )
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found.")
    return contact


@app.patch(
    "/contacts/{contact_id}",
    response_model=ContactRead,
    dependencies=[Depends(limit_contact_requests)],
)
@app.put(
    "/contacts/{contact_id}",
    response_model=ContactRead,
    dependencies=[Depends(limit_contact_requests)],
)
def update_contact(
    contact_id: int,
    payload: ContactUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Apply supplied contact fields and persist the updated record."""
    contact = db.scalar(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.owner_id == current_user.id,
        )
    )
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    return commit_contact(db, contact)


@app.delete(
    "/contacts/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(limit_contact_requests)],
)
def delete_contact(
    contact_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete an owned contact and return an empty 204 response."""
    contact = db.scalar(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.owner_id == current_user.id,
        )
    )
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found.")
    db.delete(contact)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
