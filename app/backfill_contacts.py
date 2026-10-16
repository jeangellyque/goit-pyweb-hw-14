"""Assign legacy contacts without an owner to a selected registered user."""

import argparse

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Contact, User


def assign_unowned_contacts(db: Session, owner_email: str) -> int:
    """Assign all unowned contacts to the named user and return the row count.

    Raises:
        ValueError: If no registered user has ``owner_email``.
    """
    owner = db.scalar(select(User).where(User.email == owner_email))
    if owner is None:
        raise ValueError(f"No registered user found for {owner_email}.")

    result = db.execute(
        update(Contact)
        .where(Contact.owner_id.is_(None))
        .values(owner_id=owner.id)
    )
    db.commit()
    return result.rowcount or 0


def main() -> None:
    """Parse the target email, perform the assignment, and print its count."""
    parser = argparse.ArgumentParser(
        description="Assign legacy contacts without an owner to a registered user."
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Email address of the registered user who will own legacy contacts.",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        assigned = assign_unowned_contacts(db, args.email)
    print(f"Assigned {assigned} legacy contact(s) to {args.email}.")


if __name__ == "__main__":
    main()
