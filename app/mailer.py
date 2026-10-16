"""Compose and send account verification and password-reset emails over SMTP."""

import smtplib
from email.message import EmailMessage

from app.config import EmailSettings


def _send_email(recipient: str, subject: str, body: str) -> None:
    """Send a plain-text message using the configured SMTP server."""
    settings = EmailSettings.from_env()
    message = EmailMessage()
    message["From"] = settings.from_email
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.use_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password or "")
        smtp.send_message(message)


def send_verification_email(recipient: str, token: str) -> None:
    """Send a link that verifies a user's email address."""
    settings = EmailSettings.from_env()
    _send_email(
        recipient,
        "Verify your email address",
        "Click this one-time link to verify your email address (valid for 24 hours):\n"
        f"{settings.app_base_url}/auth/verify-email?token={token}",
    )


def send_password_reset_email(recipient: str, token: str) -> None:
    """Send instructions and a single-use password-reset token."""
    settings = EmailSettings.from_env()
    _send_email(
        recipient,
        "Reset your password",
        "Use this one-time token to reset your password:\n"
        f"{token}\n\n"
        f"Submit it to {settings.app_base_url}/auth/reset-password.",
    )
