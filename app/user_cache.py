"""Redis-backed user cache with graceful fallback when Redis is unavailable."""

import json
import logging

import redis

from app.config import RedisSettings
from app.models import User


logger = logging.getLogger(__name__)


def _client() -> redis.Redis:
    """Create a Redis client using current environment configuration."""
    settings = RedisSettings.from_env()
    return redis.Redis(
        host=settings.host,
        port=settings.port,
        db=settings.database,
        password=settings.password,
        decode_responses=True,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )


def _cache_key(user_id: int) -> str:
    """Return the Redis key used for a user's cached representation."""
    return f"user:{user_id}"


def get_cached_user(user_id: int) -> User | None:
    """Load a valid cached user, discarding malformed entries as cache misses."""
    try:
        cached = _client().get(_cache_key(user_id))
    except redis.RedisError as exc:
        logger.warning("Redis unavailable while reading user cache: %s", exc)
        return None
    if cached is None:
        return None
    try:
        values = json.loads(cached)
        if (
            not isinstance(values, dict)
            or values.get("id") != user_id
            or not isinstance(values.get("email"), str)
            or not isinstance(values.get("is_email_verified"), bool)
        ):
            raise ValueError("Invalid cached user.")
        return User(
            id=values["id"],
            email=values["email"],
            is_email_verified=values["is_email_verified"],
            avatar_url=values.get("avatar_url"),
        )
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        logger.warning("Discarding invalid user cache entry for user %s.", user_id)
        invalidate_user_cache(user_id)
        return None


def cache_user(user: User) -> None:
    """Store the public user attributes with the configured cache lifetime."""
    settings = RedisSettings.from_env()
    values = {
        "id": user.id,
        "email": user.email,
        "is_email_verified": user.is_email_verified,
        "avatar_url": user.avatar_url,
    }
    try:
        _client().set(
            _cache_key(user.id),
            json.dumps(values),
            ex=settings.cache_ttl_seconds,
        )
    except redis.RedisError as exc:
        logger.warning("Redis unavailable while writing user cache: %s", exc)


def invalidate_user_cache(user_id: int) -> None:
    """Delete a user's cached record without propagating Redis failures."""
    try:
        _client().delete(_cache_key(user_id))
    except redis.RedisError as exc:
        logger.warning("Redis unavailable while invalidating user cache: %s", exc)
