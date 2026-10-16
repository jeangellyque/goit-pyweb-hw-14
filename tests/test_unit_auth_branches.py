import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth
from app.database import Base
from app.models import User
from app.schemas import UserCreate

SECRET = "a-random-secret-key-with-at-least-32-bytes"


class AuthBranchTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(
            os.environ, {"JWT_SECRET_KEY": SECRET}, clear=True
        )
        environment.start()
        self.addCleanup(environment.stop)
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        self.db = Session(engine)
        self.addCleanup(self.db.close)

    def encode(self, **claims):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": "1",
            "token_type": "access",
            "exp": now + timedelta(minutes=5),
            **claims,
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        return jwt.encode(payload, SECRET, algorithm="HS256")

    def test_decode_token_accepts_valid_token(self):
        self.assertEqual(auth.decode_token(self.encode(), "access"), 1)

    def test_decode_token_rejects_wrong_token_type(self):
        with self.assertRaises(HTTPException) as error:
            auth.decode_token(self.encode(token_type="refresh"), "access")
        self.assertEqual(error.exception.status_code, 401)

    def test_decode_token_rejects_bad_subjects(self):
        for subject in ("abc", "0", "-4"):
            with self.subTest(subject=subject):
                with self.assertRaises(HTTPException) as error:
                    auth.decode_token(self.encode(sub=subject), "access")
                self.assertEqual(error.exception.status_code, 401)

    def test_decode_token_rejects_missing_claims_and_garbage(self):
        for token in (
            self.encode(token_type=None),
            self.encode(sub=None),
            self.encode(exp=datetime.now(timezone.utc) - timedelta(minutes=1)),
            "not-a-token",
        ):
            with self.assertRaises(HTTPException):
                auth.decode_token(token, "access")

    def test_non_string_claim_types_are_unauthorized(self):
        with patch.object(auth.jwt, "decode", return_value={
            "token_type": "access",
            "sub": None,
        }):
            with self.assertRaises(HTTPException) as error:
                auth.decode_token("token", "access")
        self.assertEqual(error.exception.status_code, 401)

    def test_get_user_from_token_unknown_user(self):
        with self.assertRaises(HTTPException):
            auth.get_user_from_token(self.db, self.encode(sub="99"), "access")

    def test_get_current_user_requires_bearer_credentials(self):
        with self.assertRaises(HTTPException):
            auth.get_current_user(None, self.db)
        with self.assertRaises(HTTPException):
            auth.get_current_user(
                SimpleNamespace(scheme="Basic", credentials="x"), self.db
            )

    def test_get_current_user_prefers_cache(self):
        cached = User(id=1, email="a@example.com")
        credentials = SimpleNamespace(scheme="bearer", credentials=self.encode())
        with (
            patch.object(auth, "get_cached_user", return_value=cached),
            patch.object(auth, "cache_user") as cache_user,
        ):
            self.assertIs(auth.get_current_user(credentials, self.db), cached)
        cache_user.assert_not_called()

    def test_get_current_user_reads_db_and_populates_cache(self):
        user = User(email="a@example.com", hashed_password="x")
        self.db.add(user)
        self.db.commit()
        credentials = SimpleNamespace(
            scheme="Bearer", credentials=self.encode(sub=str(user.id))
        )
        with (
            patch.object(auth, "get_cached_user", return_value=None),
            patch.object(auth, "cache_user") as cache_user,
        ):
            self.assertIs(auth.get_current_user(credentials, self.db), user)
        cache_user.assert_called_once_with(user)

    def test_get_current_user_unknown_user_after_cache_miss(self):
        credentials = SimpleNamespace(
            scheme="bearer", credentials=self.encode(sub="42")
        )
        with patch.object(auth, "get_cached_user", return_value=None):
            with self.assertRaises(HTTPException):
                auth.get_current_user(credentials, self.db)

    def test_is_expired_handles_missing_and_naive_dates(self):
        self.assertTrue(auth._is_expired(None))
        self.assertTrue(auth._is_expired(datetime(2000, 1, 1)))
        self.assertFalse(
            auth._is_expired(datetime.now(timezone.utc) + timedelta(hours=1))
        )
        self.assertFalse(auth._is_expired((datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)))

    def test_register_commit_race_reports_conflict(self):
        db = MagicMock()
        db.scalar.side_effect = [None, 7]
        db.commit.side_effect = IntegrityError("stmt", {}, Exception("dup"))

        with self.assertRaises(HTTPException) as error:
            auth.register_user(
                db, UserCreate(email="a@example.com", password="a-secure-password")
            )

        self.assertEqual(error.exception.status_code, 409)
        db.rollback.assert_called_once_with()

    def test_register_unrelated_integrity_error_is_reraised(self):
        db = MagicMock()
        db.scalar.side_effect = [None, None]
        db.commit.side_effect = IntegrityError("stmt", {}, Exception("other"))

        with self.assertRaises(IntegrityError):
            auth.register_user(
                db, UserCreate(email="a@example.com", password="a-secure-password")
            )

        db.rollback.assert_called_once_with()

    def test_reset_and_verification_reject_unknown_tokens(self):
        with self.assertRaises(HTTPException):
            auth.verify_email_token(self.db, "unknown")
        with self.assertRaises(HTTPException):
            auth.reset_user_password(self.db, "unknown", "a-secure-password")

    def test_refresh_requires_refresh_token(self):
        with self.assertRaises(HTTPException):
            auth.create_access_token_for_refresh(self.db, self.encode())


if __name__ == "__main__":
    unittest.main()
