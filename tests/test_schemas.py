import unittest
from datetime import date

from pydantic import ValidationError

from app.schemas import (
    ContactCreate,
    ContactRead,
    ContactUpdate,
    UserCreate,
    UserLogin,
    UserRead,
)


class ContactSchemaTests(unittest.TestCase):
    def setUp(self):
        self.valid_contact = {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "phone": "+441234567890",
            "birthday": "1815-12-10",
        }

    def test_create_schema_validates_and_parses_fields(self):
        contact = ContactCreate(**self.valid_contact, extra_data="Notes")

        self.assertEqual(contact.birthday, date(1815, 12, 10))
        self.assertEqual(contact.email, "ada@example.com")
        self.assertEqual(contact.extra_data, "Notes")

    def test_create_schema_rejects_invalid_email_and_date(self):
        with self.assertRaises(ValidationError):
            ContactCreate(**{**self.valid_contact, "email": "not-an-email"})
        with self.assertRaises(ValidationError):
            ContactCreate(**{**self.valid_contact, "birthday": "not-a-date"})

    def test_create_schema_rejects_blank_required_strings_and_unknown_fields(self):
        with self.assertRaises(ValidationError):
            ContactCreate(**{**self.valid_contact, "first_name": "  "})
        with self.assertRaises(ValidationError):
            ContactCreate(**{**self.valid_contact, "unexpected": True})

    def test_update_schema_supports_partial_updates_but_rejects_null_required_fields(self):
        update = ContactUpdate(first_name="  Ada  ")

        self.assertEqual(update.model_dump(exclude_unset=True), {"first_name": "Ada"})
        with self.assertRaises(ValidationError):
            ContactUpdate(first_name=None)
        self.assertIsNone(ContactUpdate(extra_data=None).extra_data)

    def test_read_schema_builds_from_attributes(self):
        contact = ContactRead.model_validate(
            {
                "id": 1,
                **self.valid_contact,
                "birthday": date(1815, 12, 10),
                "extra_data": None,
            }
        )

        self.assertEqual(contact.id, 1)
        self.assertEqual(contact.birthday, date(1815, 12, 10))


class UserSchemaTests(unittest.TestCase):
    def test_credentials_validate_and_hide_password_in_repr(self):
        credentials = UserCreate(email="ada@example.com", password="a-secure-password")

        self.assertEqual(credentials.email, "ada@example.com")
        self.assertEqual(credentials.password.get_secret_value(), "a-secure-password")
        self.assertNotIn("a-secure-password", repr(credentials))
        self.assertEqual(
            UserLogin(email="ada@example.com", password="a-secure-password").email,
            "ada@example.com",
        )

    def test_credentials_reject_short_password_and_extra_fields(self):
        with self.assertRaises(ValidationError):
            UserCreate(email="ada@example.com", password="short")
        with self.assertRaises(ValidationError):
            UserCreate(
                email="ada@example.com",
                password="a-secure-password",
                is_admin=True,
            )

    def test_public_user_schema_excludes_password_hash(self):
        user = UserRead.model_validate(
            {
                "id": 1,
                "email": "ada@example.com",
                "hashed_password": "stored-hash",
            }
        )

        self.assertEqual(
            user.model_dump(),
            {"id": 1, "email": "ada@example.com", "avatar_url": None},
        )


if __name__ == "__main__":
    unittest.main()
