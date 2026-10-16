"""SQLAlchemy engine, declarative base, and request-scoped session dependency."""

import os
from collections.abc import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base shared by application ORM models."""

    pass


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and close it after the request completes.

    Raises:
        RuntimeError: If ``DATABASE_URL`` was not configured at startup.
    """
    if engine is None:
        raise RuntimeError("DATABASE_URL must be configured.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
