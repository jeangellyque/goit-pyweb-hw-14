import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import backfill_contacts, database
from app.backfill_contacts import assign_unowned_contacts
from app.database import Base, get_db
from app.main import commit_contact
from app.models import Contact, User


def make_contact(email="contact@example.com", owner_id=None):
    return Contact(
        first_name="Ada",
        last_name="Lovelace",
        email=email,
        phone="+441234567890",
        birthday=date(1815, 12, 10),
        owner_id=owner_id,
    )


class InMemoryDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()


class ContactRepositoryTests(InMemoryDatabaseTestCase):
    def test_commit_contact_persists_and_refreshes(self):
        contact = make_contact()
        self.db.add(contact)

        saved = commit_contact(self.db, contact)

        self.assertIsNotNone(saved.id)
        self.assertEqual(self.db.scalar(select(Contact.email)), "contact@example.com")

    def test_commit_contact_translates_duplicate_email_to_conflict(self):
        self.db.add(make_contact())
        self.db.commit()
        duplicate = make_contact()
        self.db.add(duplicate)

        with self.assertRaises(HTTPException) as error:
            commit_contact(self.db, duplicate)

        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(self.db.scalars(select(Contact)).all()), 1)

    def test_commit_contact_rolls_back_on_integrity_error(self):
        db = MagicMock()
        db.commit.side_effect = IntegrityError("stmt", {}, Exception("dup"))

        with self.assertRaises(HTTPException):
            commit_contact(db, make_contact())

        db.rollback.assert_called_once_with()
        db.refresh.assert_not_called()


class BackfillRepositoryTests(InMemoryDatabaseTestCase):
    def test_missing_owner_raises_and_leaves_contacts_untouched(self):
        self.db.add(make_contact())
        self.db.commit()

        with self.assertRaisesRegex(ValueError, "missing@example.com"):
            assign_unowned_contacts(self.db, "missing@example.com")

        self.assertIsNone(self.db.scalar(select(Contact.owner_id)))

    def test_noop_when_all_contacts_are_owned(self):
        owner = User(email="ada@example.com", hashed_password="hash")
        other = User(email="bob@example.com", hashed_password="hash")
        self.db.add_all([owner, other])
        self.db.commit()
        self.db.add(make_contact(owner_id=other.id))
        self.db.commit()

        self.assertEqual(assign_unowned_contacts(self.db, "ada@example.com"), 0)
        self.assertEqual(self.db.scalar(select(Contact.owner_id)), other.id)

    def test_assigns_only_unowned_contacts(self):
        owner = User(email="ada@example.com", hashed_password="hash")
        other = User(email="bob@example.com", hashed_password="hash")
        self.db.add_all([owner, other])
        self.db.commit()
        self.db.add_all(
            [
                make_contact("a@example.com"),
                make_contact("b@example.com"),
                make_contact("c@example.com", owner_id=other.id),
            ]
        )
        self.db.commit()

        self.assertEqual(assign_unowned_contacts(self.db, "ada@example.com"), 2)
        owners = dict(self.db.execute(select(Contact.email, Contact.owner_id)).all())
        self.assertEqual(owners["a@example.com"], owner.id)
        self.assertEqual(owners["c@example.com"], other.id)

    def test_main_reports_assigned_count(self):
        session = MagicMock()
        session_factory = MagicMock()
        session_factory.return_value.__enter__.return_value = session
        with (
            patch("sys.argv", ["backfill", "--email", "ada@example.com"]),
            patch.object(backfill_contacts, "SessionLocal", session_factory),
            patch.object(
                backfill_contacts, "assign_unowned_contacts", return_value=3
            ) as assign,
            patch("builtins.print") as printed,
        ):
            backfill_contacts.main()

        assign.assert_called_once_with(session, "ada@example.com")
        printed.assert_called_once_with(
            "Assigned 3 legacy contact(s) to ada@example.com."
        )

    def test_main_requires_email_argument(self):
        with patch("sys.argv", ["backfill"]), self.assertRaises(SystemExit):
            backfill_contacts.main()


class DatabaseDependencyTests(unittest.TestCase):
    def test_get_db_requires_configured_engine(self):
        with patch.object(database, "engine", None):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                next(get_db())

    def test_get_db_closes_session_when_consumer_raises(self):
        session = MagicMock()
        with (
            patch.object(database, "engine", object()),
            patch.object(database, "SessionLocal", return_value=session),
        ):
            dependency = get_db()
            self.assertIs(next(dependency), session)
            with self.assertRaises(RuntimeError):
                dependency.throw(RuntimeError("boom"))

        session.close.assert_called_once_with()

    def test_get_db_closes_session_on_normal_exhaustion(self):
        session = MagicMock()
        with (
            patch.object(database, "engine", object()),
            patch.object(database, "SessionLocal", return_value=session),
        ):
            dependency = get_db()
            next(dependency)
            with self.assertRaises(StopIteration):
                next(dependency)

        session.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
