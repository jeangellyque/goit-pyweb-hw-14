import unittest
from unittest.mock import patch

from sqlalchemy import Date

from app.database import get_db
from app.models import Contact, User


class DatabaseTests(unittest.TestCase):
    def test_contact_model_has_required_fields_and_unique_email(self):
        columns = Contact.__table__.columns

        self.assertEqual(
            set(columns.keys()),
            {
                "id",
                "first_name",
                "last_name",
                "email",
                "phone",
                "birthday",
                "extra_data",
                "owner_id",
            },
        )
        self.assertIsInstance(columns["birthday"].type, Date)
        self.assertTrue(columns["email"].unique)
        self.assertTrue(
            next(
                index
                for index in Contact.__table__.indexes
                if "email" in {column.name for column in index.columns}
            ).unique
        )
        self.assertTrue(columns["extra_data"].nullable)
        self.assertTrue(columns["owner_id"].nullable)
        self.assertIn(
            "users.id",
            {str(key.column) for key in columns["owner_id"].foreign_keys},
        )

    def test_user_model_stores_unique_email_and_password_hash(self):
        columns = User.__table__.columns

        self.assertEqual(
            set(columns.keys()),
            {
                "id",
                "email",
                "hashed_password",
                "is_email_verified",
                "email_verification_token_hash",
                "email_verification_expires_at",
                "password_reset_token_hash",
                "password_reset_expires_at",
                "avatar_url",
            },
        )
        self.assertTrue(columns["email"].unique)
        self.assertTrue(
            next(
                index
                for index in User.__table__.indexes
                if "email" in {column.name for column in index.columns}
            ).unique
        )
        self.assertFalse(columns["hashed_password"].nullable)

    def test_database_dependency_closes_session(self):
        class StubSession:
            closed = False

            def close(self):
                self.closed = True

        session = StubSession()
        with (
            patch("app.database.engine", object()),
            patch("app.database.SessionLocal", return_value=session),
        ):
            dependency = get_db()
            self.assertIs(next(dependency), session)
            dependency.close()

        self.assertTrue(session.closed)
