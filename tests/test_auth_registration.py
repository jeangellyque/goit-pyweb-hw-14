import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.routing import APIRoute
from pwdlib import PasswordHash
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base, get_db
from app.main import app, create_user
from app.models import User
from app.schemas import UserCreate, UserRead


class UserRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.email_sender = patch("app.main.send_verification_email")
        self.email_sender.start()

    def tearDown(self):
        self.email_sender.stop()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_register_creates_user_with_hashed_password_and_201(self):
        user = create_user(
            UserCreate(email="ada@example.com", password="a-secure-password"),
            self.db,
        )
        response = UserRead.model_validate(user)

        self.assertEqual(
            response.model_dump(exclude_none=True),
            {"id": 1, "email": "ada@example.com"},
        )
        user = self.db.scalar(select(User))
        self.assertIsNotNone(user)
        self.assertFalse(user.is_email_verified)
        self.assertNotEqual(user.hashed_password, "a-secure-password")
        self.assertTrue(
            PasswordHash.recommended().verify(
                "a-secure-password",
                user.hashed_password,
            )
        )
        self.assertNotIn("hashed_password", response.model_dump())
        route = next(
            route
            for route in app.routes
            if isinstance(route, APIRoute) and route.path == "/auth/register"
        )
        self.assertEqual(route.status_code, 201)
        self.assertEqual(route.response_model, UserRead)

    def test_register_returns_409_for_duplicate_email(self):
        payload = UserCreate(email="ada@example.com", password="a-secure-password")
        create_user(payload, self.db)

        with self.assertRaises(HTTPException) as error:
            create_user(payload, self.db)
        self.assertEqual(error.exception.status_code, 409)

    def test_register_treats_email_case_insensitively(self):
        create_user(
            UserCreate(email="ada@example.com", password="a-secure-password"),
            self.db,
        )

        with self.assertRaises(HTTPException) as error:
            create_user(
                UserCreate(email="ADA@example.com", password="another-password"),
                self.db,
            )
        self.assertEqual(error.exception.status_code, 409)

    def test_register_rejects_invalid_credentials(self):
        with self.assertRaises(ValidationError):
            UserCreate(email="not-an-email", password="short")
        self.assertIsNone(self.db.scalar(select(User)))


if __name__ == "__main__":
    unittest.main()
