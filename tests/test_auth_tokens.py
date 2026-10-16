import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token_for_refresh,
    create_token_pair,
    decode_token,
    get_current_user,
)
from app.database import Base
from app.main import app, create_user, login
from app.models import User
from app.schemas import (
    AccessTokenResponse,
    RefreshTokenRequest,
    TokenPair,
    UserCreate,
    UserLogin,
)


class AuthenticationTokenTests(unittest.TestCase):
    jwt_environment = {
        "JWT_SECRET_KEY": "a-random-secret-key-with-at-least-32-bytes",
        "JWT_ALGORITHM": "HS256",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
    }

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.environment = patch.dict(os.environ, self.jwt_environment, clear=True)
        self.environment.start()
        self.email_sender = patch("app.main.send_verification_email")
        self.email_sender.start()
        self.user = create_user(
            UserCreate(email="ada@example.com", password="a-secure-password"),
            self.db,
        )
        self.user.is_email_verified = True
        self.db.commit()

    def tearDown(self):
        self.email_sender.stop()
        self.environment.stop()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def encode_token(self, token_type: str, expires_at: datetime) -> str:
        return jwt.encode(
            {
                "sub": str(self.user.id),
                "token_type": token_type,
                "iat": datetime.now(timezone.utc),
                "exp": expires_at,
            },
            self.jwt_environment["JWT_SECRET_KEY"],
            algorithm=self.jwt_environment["JWT_ALGORITHM"],
        )

    def test_login_returns_access_and_refresh_tokens(self):
        response = login(
            UserLogin(email="ada@example.com", password="a-secure-password"),
            self.db,
        )
        token_pair = TokenPair.model_validate(response)

        self.assertEqual(token_pair.token_type, "bearer")
        self.assertEqual(token_pair.expires_in, 15 * 60)
        self.assertEqual(token_pair.refresh_expires_in, 7 * 24 * 60 * 60)
        self.assertEqual(decode_token(token_pair.access_token, "access"), self.user.id)
        self.assertEqual(decode_token(token_pair.refresh_token, "refresh"), self.user.id)
        self.assertNotEqual(token_pair.access_token, token_pair.refresh_token)

    def test_login_returns_401_for_unknown_user_or_wrong_password(self):
        invalid_logins = (
            UserLogin(email="missing@example.com", password="a-secure-password"),
            UserLogin(email="ada@example.com", password="wrong-password"),
        )

        for credentials in invalid_logins:
            with self.subTest(email=credentials.email):
                with self.assertRaises(HTTPException) as error:
                    login(credentials, self.db)
                self.assertEqual(error.exception.status_code, 401)
                self.assertEqual(error.exception.headers["WWW-Authenticate"], "Bearer")

    def test_refresh_issues_only_a_new_access_token(self):
        token_pair = create_token_pair(self.user.id)

        response = create_access_token_for_refresh(
            self.db,
            RefreshTokenRequest(refresh_token=token_pair["refresh_token"]).refresh_token,
        )
        access_response = AccessTokenResponse.model_validate(response)

        self.assertEqual(access_response.token_type, "bearer")
        self.assertEqual(access_response.expires_in, 15 * 60)
        self.assertEqual(decode_token(access_response.access_token, "access"), self.user.id)
        self.assertNotIn("refresh_token", access_response.model_dump())

    def test_refresh_rejects_expired_refresh_token(self):
        expired_token = self.encode_token(
            "refresh",
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )

        with self.assertRaises(HTTPException) as error:
            create_access_token_for_refresh(self.db, expired_token)
        self.assertEqual(error.exception.status_code, 401)

    def test_access_and_refresh_tokens_cannot_be_interchanged(self):
        token_pair = create_token_pair(self.user.id)

        with self.assertRaises(HTTPException) as refresh_error:
            create_access_token_for_refresh(self.db, token_pair["access_token"])
        self.assertEqual(refresh_error.exception.status_code, 401)

        refresh_credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=token_pair["refresh_token"],
        )
        with self.assertRaises(HTTPException) as access_error:
            get_current_user(refresh_credentials, self.db)
        self.assertEqual(access_error.exception.status_code, 401)

    def test_missing_invalid_expired_and_deleted_user_tokens_return_401(self):
        expired_token = self.encode_token(
            "access",
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        invalid_tokens = (
            "not-a-jwt",
            self.encode_token(
                "refresh",
                datetime.now(timezone.utc) + timedelta(minutes=1),
            ),
            expired_token,
        )

        with self.assertRaises(HTTPException) as missing_error:
            get_current_user(None, self.db)
        self.assertEqual(missing_error.exception.status_code, 401)

        for token in invalid_tokens:
            with self.subTest(token=token):
                credentials = HTTPAuthorizationCredentials(
                    scheme="Bearer",
                    credentials=token,
                )
                with self.assertRaises(HTTPException) as error:
                    get_current_user(credentials, self.db)
                self.assertEqual(error.exception.status_code, 401)

        self.db.delete(self.user)
        self.db.commit()
        deleted_user_token = self.encode_token(
            "access",
            datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=deleted_user_token,
        )
        with self.assertRaises(HTTPException) as deleted_user_error:
            get_current_user(credentials, self.db)
        self.assertEqual(deleted_user_error.exception.status_code, 401)

    def test_auth_routes_have_expected_response_schemas(self):
        routes = {
            route.path: route
            for route in app.routes
            if isinstance(route, APIRoute)
        }

        self.assertEqual(routes["/auth/login"].status_code or 200, 200)
        self.assertEqual(routes["/auth/login"].response_model, TokenPair)
        self.assertEqual(routes["/auth/refresh"].status_code or 200, 200)
        self.assertEqual(
            routes["/auth/refresh"].response_model,
            AccessTokenResponse,
        )


if __name__ == "__main__":
    unittest.main()
