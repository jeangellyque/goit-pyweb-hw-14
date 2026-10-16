"""Thread-safe in-memory rate limits for contact API requests."""

from collections import deque
import os
from threading import Lock
from time import monotonic

from fastapi import Depends, HTTPException, Request, status

from app.auth import get_current_user
from app.config import _positive_int_with_default
from app.models import User


class InMemoryRateLimiter:
    """Track request timestamps and reject keys exceeding a sliding window."""

    def __init__(self) -> None:
        """Initialize independent request histories and their synchronization lock."""
        self._requests: dict[str, deque[float]] = {}
        self._lock = Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        """Record a request or raise HTTP 429 with the retry delay."""
        now = monotonic()
        cutoff = now - window_seconds
        with self._lock:
            requests = self._requests.setdefault(key, deque())
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= limit:
                retry_after = max(1, int(window_seconds - (now - requests[0])))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Please try again later.",
                    headers={"Retry-After": str(retry_after)},
                )
            requests.append(now)


contact_limiter = InMemoryRateLimiter()
create_contact_limiter = InMemoryRateLimiter()


def limit_contact_requests(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> None:
    """Enforce the per-user, HTTP-method, route request limit."""
    limit = _positive_int_with_default("CONTACT_REQUESTS_PER_MINUTE", 60)
    route = request.scope.get("route")
    route_path = getattr(route, "path", request.url.path)
    contact_limiter.check(
        f"{current_user.id}:{request.method}:{route_path}",
        limit,
        60,
    )


def limit_contact_creation(
    current_user: User = Depends(get_current_user),
) -> None:
    """Enforce the additional per-user contact-creation limit."""
    limit = _positive_int_with_default("CONTACT_CREATES_PER_MINUTE", 10)
    create_contact_limiter.check(f"user:{current_user.id}", limit, 60)
