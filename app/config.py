"""Environment-backed, validated settings for application integrations."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()

_SUPPORTED_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})


def _positive_int_from_env(name: str) -> int:
    """Read a required positive integer environment variable."""
    value = os.getenv(name)
    if value is None:
        raise ValueError(f"{name} must be set.")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return parsed


def _positive_int_with_default(name: str, default: int) -> int:
    """Read a positive integer environment variable or use its default."""
    value = os.getenv(name, str(default))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return parsed


def _bool_from_env(name: str, default: bool = False) -> bool:
    """Parse a supported textual boolean value from the environment."""
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be a boolean.")
    return normalized in {"true", "1", "yes"}


@dataclass(frozen=True)
class JWTSettings:
    """Validated signing algorithm, secret, and JWT lifetimes."""

    secret_key: str
    algorithm: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int

    @classmethod
    def from_env(cls) -> "JWTSettings":
        """Read and validate the JWT settings from environment variables."""
        secret_key = os.getenv("JWT_SECRET_KEY", "")
        if len(secret_key.encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET_KEY must contain at least 32 bytes.")

        algorithm = os.getenv("JWT_ALGORITHM", "HS256")
        if algorithm not in _SUPPORTED_JWT_ALGORITHMS:
            supported = ", ".join(sorted(_SUPPORTED_JWT_ALGORITHMS))
            raise ValueError(f"JWT_ALGORITHM must be one of: {supported}.")

        return cls(
            secret_key=secret_key,
            algorithm=algorithm,
            access_token_expire_minutes=_positive_int_with_default(
                "JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 15
            ),
            refresh_token_expire_days=_positive_int_with_default(
                "JWT_REFRESH_TOKEN_EXPIRE_DAYS", 7
            ),
        )


@dataclass(frozen=True)
class EmailSettings:
    """SMTP connection details and public application URL."""

    smtp_host: str
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    from_email: str
    use_starttls: bool
    app_base_url: str

    @classmethod
    def from_env(cls) -> "EmailSettings":
        """Read SMTP settings, applying local-development defaults."""
        port = (
            _positive_int_from_env("SMTP_PORT")
            if os.getenv("SMTP_PORT")
            else 1025
        )
        return cls(
            smtp_host=os.getenv("SMTP_HOST", "localhost"),
            smtp_port=port,
            smtp_username=os.getenv("SMTP_USERNAME") or None,
            smtp_password=os.getenv("SMTP_PASSWORD") or None,
            from_email=os.getenv("SMTP_FROM_EMAIL", "no-reply@example.com"),
            use_starttls=_bool_from_env("SMTP_USE_STARTTLS"),
            app_base_url=os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip(
                "/"
            ),
        )


@dataclass(frozen=True)
class CORSSettings:
    """Allowed browser origins parsed from a comma-separated value."""

    allowed_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "CORSSettings":
        """Return the configured list of allowed CORS origins."""
        origins = os.getenv("ALLOWED_ORIGINS", "")
        return cls(
            allowed_origins=tuple(
                origin.strip() for origin in origins.split(",") if origin.strip()
            )
        )


@dataclass(frozen=True)
class RedisSettings:
    """Redis connection details and user-cache expiration."""

    host: str
    port: int
    database: int
    password: str | None
    cache_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "RedisSettings":
        """Read and validate the Redis settings from environment variables."""
        return cls(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=_positive_int_with_default("REDIS_PORT", 6379),
            database=int(os.getenv("REDIS_DB", "0")),
            password=os.getenv("REDIS_PASSWORD") or None,
            cache_ttl_seconds=_positive_int_with_default(
                "REDIS_CACHE_TTL_SECONDS", 900
            ),
        )


@dataclass(frozen=True)
class CloudinarySettings:
    """Required credentials for the avatar image service."""

    cloud_name: str
    api_key: str
    api_secret: str

    @classmethod
    def from_env(cls) -> "CloudinarySettings":
        """Read all required Cloudinary credentials or report missing values."""
        values = {
            "CLOUDINARY_CLOUD_NAME": os.getenv("CLOUDINARY_CLOUD_NAME", "").strip(),
            "CLOUDINARY_API_KEY": os.getenv("CLOUDINARY_API_KEY", "").strip(),
            "CLOUDINARY_API_SECRET": os.getenv("CLOUDINARY_API_SECRET", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(f"Missing Cloudinary settings: {', '.join(missing)}.")
        return cls(
            cloud_name=values["CLOUDINARY_CLOUD_NAME"],
            api_key=values["CLOUDINARY_API_KEY"],
            api_secret=values["CLOUDINARY_API_SECRET"],
        )
