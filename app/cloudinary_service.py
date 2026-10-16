"""Validate avatar uploads and store them with Cloudinary."""

from typing import BinaryIO

import cloudinary.uploader

from app.config import CloudinarySettings


MAX_AVATAR_SIZE_BYTES = 5 * 1024 * 1024
ALLOWED_AVATAR_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


class InvalidAvatarError(ValueError):
    """Raised when an avatar does not meet the accepted upload constraints."""

    pass


def upload_avatar(file: BinaryIO, content_type: str | None) -> str:
    """Upload an accepted image and return its HTTPS CDN URL.

    Raises:
        InvalidAvatarError: If the media type or file size is not allowed.
        ValueError: If Cloudinary configuration is incomplete.
        RuntimeError: If Cloudinary does not return a secure URL.
    """
    if content_type not in ALLOWED_AVATAR_TYPES:
        raise InvalidAvatarError("Avatar must be a JPEG, PNG, or WebP image.")

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size == 0 or size > MAX_AVATAR_SIZE_BYTES:
        raise InvalidAvatarError("Avatar must be between 1 byte and 5 MB.")

    settings = CloudinarySettings.from_env()
    result = cloudinary.uploader.upload(
        file,
        resource_type="image",
        folder="avatars",
        overwrite=True,
        cloud_name=settings.cloud_name,
        api_key=settings.api_key,
        api_secret=settings.api_secret,
    )
    url = result.get("secure_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise RuntimeError("Cloudinary did not return a secure avatar URL.")
    return url
