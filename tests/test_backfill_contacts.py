import unittest
from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.backfill_contacts import assign_unowned_contacts
from app.database import Base
from app.models import Contact, User


class LegacyContactBackfillTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(
            email="ada@example.com",
            hashed_password="hashed-password",
        )
        self.contact = Contact(
            first_name="Ada",
            last_name="Lovelace",
            email="contact@example.com",
            phone="+441234567890",
            birthday=date(1815, 12, 10),
        )
        self.db.add_all([self.user, self.contact])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_assigns_only_contacts_without_owner(self):
        assigned = assign_unowned_contacts(self.db, self.user.email)

        self.assertEqual(assigned, 1)
        self.assertEqual(self.db.scalar(select(Contact)).owner_id, self.user.id)

    def test_requires_existing_registered_user(self):
        with self.assertRaisesRegex(ValueError, "No registered user found"):
            assign_unowned_contacts(self.db, "missing@example.com")

        self.assertIsNone(self.db.scalar(select(Contact)).owner_id)


if __name__ == "__main__":
    unittest.main()
