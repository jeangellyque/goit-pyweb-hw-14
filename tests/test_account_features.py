import hashlib
import os
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.datastructures import Headers
from starlette.datastructures import UploadFile
from starlette.requests import Request

from app.auth import authenticate_user, get_current_user
from app.cloudinary_service import InvalidAvatarError, upload_avatar
from app.config import CORSSettings
from app.database import Base
from app.main import (
    app,
    confirm_password_reset,
    create_user,
    login,
    request_password_reset,
    resend_verification_email,
    update_avatar,
    verify_email,
    verify_email_link,
)
from app.models import User
from app.rate_limit import (
    contact_limiter,
    create_contact_limiter,
    limit_contact_creation,
    limit_contact_requests,
)
from app.schemas import (
    EmailAddressRequest,
    EmailTokenRequest,
    PasswordResetConfirm,
    UserCreate,
    UserLogin,
)


class AccountFeatureTests(unittest.TestCase):
    jwt_environment = {
        "JWT_SECRET_KEY": "a-random-secret-key-with-at-least-32-bytes",
        "JWT_ALGORITHM": "HS256",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
    }

    def setUp(self):
        self.environment = patch.dict(os.environ, self.jwt_environment, clear=True)
        self.environment.start()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.verification_messages = []
        self.reset_messages = []
        self.verification_sender = patch(
            "app.main.send_verification_email",
            side_effect=lambda email, token: self.verification_messages.append(
                (email, token)
            ),
        )
        self.reset_sender = patch(
            "app.main.send_password_reset_email",
            side_effect=lambda email, token: self.reset_messages.append(
                (email, token)
            ),
        )
        self.verification_sender.start()
        self.reset_sender.start()

    def tearDown(self):
        self.reset_sender.stop()
        self.verification_sender.stop()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        self.environment.stop()

    def register(self, email="ada@example.com"):
        return create_user(
            UserCreate(email=email, password="a-secure-password"),
            self.db,
        )

    def test_verification_blocks_login_and_resend_rotates_single_use_token(self):
        user = self.register()
        original_token = self.verification_messages[-1][1]
        with self.assertRaises(HTTPException) as login_error:
            authenticate_user(
                self.db,
                UserLogin(email=user.email, password="a-secure-password"),
            )
        self.assertEqual(login_error.exception.status_code, 403)

        resend_verification_email(
            EmailAddressRequest(email=user.email),
            self.db,
        )
        replacement_token = self.verification_messages[-1][1]
        self.assertNotEqual(original_token, replacement_token)
        with self.assertRaises(HTTPException):
            verify_email(EmailTokenRequest(token=original_token), self.db)

        verified_user = verify_email_link(replacement_token, self.db)
        self.assertTrue(verified_user.is_email_verified)
        self.assertIsNone(verified_user.email_verification_token_hash)
        self.assertEqual(login(
            UserLogin(email=user.email, password="a-secure-password"),
            self.db,
        )["token_type"], "bearer")
        with self.assertRaises(HTTPException):
            verify_email(EmailTokenRequest(token=replacement_token), self.db)

    def test_expired_email_verification_token_is_rejected(self):
        user = self.register()
        token = "expired-verification-token"
        user.email_verification_token_hash = hashlib.sha256(token.encode()).hexdigest()
        user.email_verification_expires_at = datetime.now(timezone.utc) - timedelta(
            seconds=1
        )
        self.db.commit()

        with self.assertRaises(HTTPException) as error:
            verify_email(EmailTokenRequest(token=token), self.db)
        self.assertEqual(error.exception.status_code, 400)

    def test_password_reset_is_generic_and_token_is_single_use(self):
        user = self.register()
        verify_email(
            EmailTokenRequest(token=self.verification_messages[-1][1]),
            self.db,
        )
        missing_response = request_password_reset(
            EmailAddressRequest(email="missing@example.com"),
            self.db,
        )
        existing_response = request_password_reset(
            EmailAddressRequest(email=user.email),
            self.db,
        )
        self.assertEqual(missing_response, existing_response)
        token = self.reset_messages[-1][1]

        response = confirm_password_reset(
            PasswordResetConfirm(token=token, new_password="a-new-secure-password"),
            self.db,
        )
        self.assertEqual(response["detail"], "Password has been reset.")
        self.assertIsNone(user.password_reset_token_hash)
        self.assertEqual(
            authenticate_user(
                self.db,
                UserLogin(email=user.email, password="a-new-secure-password"),
            ).id,
            user.id,
        )
        with self.assertRaises(HTTPException):
            authenticate_user(
                self.db,
                UserLogin(email=user.email, password="a-secure-password"),
            )
        with self.assertRaises(HTTPException) as error:
            confirm_password_reset(
                PasswordResetConfirm(
                    token=token,
                    new_password="another-secure-password",
                ),
                self.db,
            )
        self.assertEqual(error.exception.status_code, 400)

    def test_expired_password_reset_token_is_rejected(self):
        user = self.register()
        token = "expired-password-reset-token"
        user.password_reset_token_hash = hashlib.sha256(token.encode()).hexdigest()
        user.password_reset_expires_at = datetime.now(timezone.utc) - timedelta(
            seconds=1
        )
        self.db.commit()

        with self.assertRaises(HTTPException) as error:
            confirm_password_reset(
                PasswordResetConfirm(token=token, new_password="a-new-secure-password"),
                self.db,
            )
        self.assertEqual(error.exception.status_code, 400)

    def test_contact_creation_limit_is_per_user(self):
        user = User(
            email="rate-limited@example.com",
            hashed_password="hashed",
            is_email_verified=True,
        )
        create_contact_limiter._requests.clear()
        with patch.dict(os.environ, {"CONTACT_CREATES_PER_MINUTE": "1"}):
            limit_contact_creation(user)
            with self.assertRaises(HTTPException) as error:
                limit_contact_creation(user)
            other_user = User(
                id=43,
                email="another@example.com",
                hashed_password="hashed",
                is_email_verified=True,
            )
            limit_contact_creation(other_user)
        self.assertEqual(error.exception.status_code, 429)
        self.assertIn("Retry-After", error.exception.headers)

    def test_contact_request_limit_is_per_user_route_and_method(self):
        user = User(
            id=42,
            email="rate-limited@example.com",
            hashed_password="hashed",
            is_email_verified=True,
        )
        other_user = User(
            id=43,
            email="another@example.com",
            hashed_password="hashed",
            is_email_verified=True,
        )
        contact_limiter._requests.clear()

        def make_request(method, path, route_path):
            return Request(
                {
                    "type": "http",
                    "method": method,
                    "path": path,
                    "headers": [],
                    "query_string": b"",
                    "server": ("testserver", 80),
                    "client": ("testclient", 50000),
                    "scheme": "http",
                    "root_path": "",
                    "route": SimpleNamespace(path=route_path),
                }
            )

        with patch.dict(os.environ, {"CONTACT_REQUESTS_PER_MINUTE": "1"}):
            list_request = make_request("GET", "/contacts", "/contacts")
            limit_contact_requests(list_request, user)
            with self.assertRaises(HTTPException) as error:
                limit_contact_requests(list_request, user)
            limit_contact_requests(
                make_request("GET", "/contacts/birthdays", "/contacts/birthdays"),
                user,
            )
            limit_contact_requests(
                make_request("POST", "/contacts", "/contacts"),
                user,
            )
            limit_contact_requests(list_request, other_user)

        self.assertEqual(error.exception.status_code, 429)
        self.assertIn("Retry-After", error.exception.headers)

    def test_cors_settings_parse_comma_separated_origins(self):
        with patch.dict(
            os.environ,
            {"ALLOWED_ORIGINS": " https://app.example, http://localhost:3000 "},
        ):
            settings = CORSSettings.from_env()
        self.assertEqual(
            settings.allowed_origins,
            ("https://app.example", "http://localhost:3000"),
        )
        self.assertTrue(
            any(middleware.cls is CORSMiddleware for middleware in app.user_middleware)
        )

    def test_cached_user_is_used_for_authorization(self):
        from app.auth import create_token

        user = User(
            id=42,
            email="cached@example.com",
            hashed_password="not-needed",
            is_email_verified=True,
        )
        credentials_token = create_token(42, "access", timedelta(minutes=5))
        from fastapi.security import HTTPAuthorizationCredentials

        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=credentials_token,
        )
        with patch("app.auth.get_cached_user", return_value=user), patch(
            "app.auth.cache_user"
        ) as cache_writer:
            current = get_current_user(credentials, self.db)
        self.assertEqual(current.email, "cached@example.com")
        cache_writer.assert_not_called()

    def test_avatar_upload_validates_file_and_returns_cloudinary_url(self):
        cloudinary_environment = {
            "CLOUDINARY_CLOUD_NAME": "test-cloud",
            "CLOUDINARY_API_KEY": "test-key",
            "CLOUDINARY_API_SECRET": "test-secret",
        }
        with patch.dict(os.environ, cloudinary_environment):
            with patch(
                "app.cloudinary_service.cloudinary.uploader.upload",
                return_value={"secure_url": "https://res.cloudinary.com/test/avatar.jpg"},
            ) as upload:
                url = upload_avatar(BytesIO(b"image-data"), "image/jpeg")
        self.assertEqual(url, "https://res.cloudinary.com/test/avatar.jpg")
        upload.assert_called_once()
        self.assertEqual(upload.call_args.kwargs["folder"], "avatars")
        with self.assertRaises(InvalidAvatarError):
            upload_avatar(BytesIO(b"image-data"), "text/plain")
        with self.assertRaises(InvalidAvatarError):
            upload_avatar(
                BytesIO(b"x" * (5 * 1024 * 1024 + 1)),
                "image/jpeg",
            )

        with patch.dict(os.environ, cloudinary_environment):
            with patch(
                "app.cloudinary_service.cloudinary.uploader.upload",
                return_value={"secure_url": "http://insecure.example/avatar.jpg"},
            ):
                with self.assertRaises(RuntimeError):
                    upload_avatar(BytesIO(b"image-data"), "image/jpeg")

    def test_avatar_route_persists_url_and_invalidates_user_cache(self):
        user = self.register()
        image = UploadFile(
            filename="avatar.png",
            file=BytesIO(b"image-data"),
            headers=Headers({"content-type": "image/png"}),
        )
        with patch(
            "app.main.upload_avatar",
            return_value="https://res.cloudinary.com/test/avatar.png",
        ), patch("app.main.invalidate_user_cache") as invalidate:
            result = update_avatar(image, user, self.db)

        self.assertEqual(
            result.avatar_url,
            "https://res.cloudinary.com/test/avatar.png",
        )
        invalidate.assert_called_once_with(user.id)

    def test_redis_user_cache_stores_ttl_and_can_invalidate(self):
        from app.user_cache import cache_user, get_cached_user, invalidate_user_cache

        class FakeRedis:
            values = {}
            ttl = None

            def get(self, key):
                return self.values.get(key)

            def set(self, key, value, ex):
                self.values[key] = value
                self.ttl = ex

            def delete(self, key):
                self.values.pop(key, None)

        fake_redis = FakeRedis()
        user = User(
            id=42,
            email="cached@example.com",
            hashed_password="not-cached",
            is_email_verified=True,
            avatar_url="https://res.cloudinary.com/test/avatar.png",
        )
        with patch("app.user_cache._client", return_value=fake_redis):
            cache_user(user)
            cached = get_cached_user(user.id)
            invalidate_user_cache(user.id)

        self.assertEqual(fake_redis.ttl, 900)
        self.assertEqual(cached.email, user.email)
        self.assertEqual(cached.avatar_url, user.avatar_url)
        self.assertIsNone(fake_redis.values.get("user:42"))


if __name__ == "__main__":
    unittest.main()
