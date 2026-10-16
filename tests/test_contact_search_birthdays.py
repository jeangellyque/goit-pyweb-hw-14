import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.main import list_contacts, list_upcoming_birthdays
from app.models import Contact, User


class ContactSearchAndBirthdayTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(email="owner@example.com", hashed_password="already-hashed")
        self.other_user = User(email="other@example.com", hashed_password="already-hashed")
        self.db.add_all([self.user, self.other_user])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def add_contact(self, first_name, last_name, email, birthday):
        contact = Contact(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone="555-0100",
            birthday=birthday,
            owner_id=self.user.id,
        )
        self.db.add(contact)
        self.db.commit()
        self.db.refresh(contact)
        return contact

    def test_search_matches_first_name_last_name_or_email(self):
        ada = self.add_contact("Ada", "Lovelace", "ada@example.com", date(1815, 12, 10))
        grace = self.add_contact("Grace", "Hopper", "grace@example.com", date(1906, 12, 9))

        def search(term):
            return list_contacts(
                search=term,
                first_name=None,
                last_name=None,
                email=None,
                current_user=self.user,
                db=self.db,
            )

        self.assertEqual(search("ada"), [ada])
        self.assertEqual(search("HOPPER"), [grace])
        self.assertEqual(search("EXAMPLE.COM"), [ada, grace])

    def test_upcoming_birthdays_cover_seven_day_window_across_new_year(self):
        today = self.add_contact("Today", "Birthday", "today@example.com", date(1990, 12, 30))
        last_day = self.add_contact("Sixth", "Day", "sixth@example.com", date(1990, 1, 5))
        outside = self.add_contact("Seventh", "Day", "seventh@example.com", date(1990, 1, 6))

        with patch("app.main.date") as mocked_date:
            mocked_date.today.return_value = date(2024, 12, 30)
            upcoming = list_upcoming_birthdays(self.user, self.db)

        self.assertEqual(upcoming, [today, last_day])
        self.assertNotIn(outside, upcoming)

    def test_search_and_upcoming_birthdays_exclude_other_users_contacts(self):
        own_contact = self.add_contact(
            "Ada",
            "Lovelace",
            "ada@example.com",
            date(1990, 12, 30),
        )
        other_contact = Contact(
            first_name="Grace",
            last_name="Hopper",
            email="grace@example.com",
            phone="555-0101",
            birthday=date(1906, 12, 30),
            owner_id=self.other_user.id,
        )
        self.db.add(other_contact)
        self.db.commit()

        matches = list_contacts(
            search=None,
            first_name=None,
            last_name=None,
            email=None,
            current_user=self.user,
            db=self.db,
        )
        with patch("app.main.date") as mocked_date:
            mocked_date.today.return_value = date(2024, 12, 30)
            birthdays = list_upcoming_birthdays(self.user, self.db)

        self.assertEqual(matches, [own_contact])
        self.assertEqual(birthdays, [own_contact])
        self.assertNotIn(other_contact, matches)
        self.assertNotIn(other_contact, birthdays)


if __name__ == "__main__":
    unittest.main()
