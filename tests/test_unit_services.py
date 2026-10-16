import json
import os
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import redis
from fastapi import HTTPException
from starlette.requests import Request

from app import cloudinary_service, mailer, rate_limit, user_cache
from app.config import (
    CloudinarySettings,
    CORSSettings,
    EmailSettings,
    JWTSettings,
    RedisSettings,
    _bool_from_env,
    _positive_int_from_env,
    _positive_int_with_default,
)
from app.models import User


_REAL_CLIENT = user_cache._client


def env(**values):
    return patch.dict(os.environ, values, clear=True)


class ConfigTests(unittest.TestCase):
    def test_positive_int_from_env(self):
        with env(VALUE="5"):
            self.assertEqual(_positive_int_from_env("VALUE"), 5)
        with env():
            with self.assertRaisesRegex(ValueError, "must be set"):
                _positive_int_from_env("VALUE")
        for bad in ("abc", "0", "-3"):
            with env(VALUE=bad), self.assertRaisesRegex(ValueError, "positive"):
                _positive_int_from_env("VALUE")

    def test_positive_int_with_default(self):
        with env():
            self.assertEqual(_positive_int_with_default("VALUE", 9), 9)
        with env(VALUE="4"):
            self.assertEqual(_positive_int_with_default("VALUE", 9), 4)
        for bad in ("abc", "0", "-1"):
            with env(VALUE=bad), self.assertRaisesRegex(ValueError, "positive"):
                _positive_int_with_default("VALUE", 9)

    def test_bool_from_env(self):
        with env():
            self.assertFalse(_bool_from_env("FLAG"))
            self.assertTrue(_bool_from_env("FLAG", True))
        for value in ("true", "1", " YES "):
            with env(FLAG=value):
                self.assertTrue(_bool_from_env("FLAG"))
        for value in ("false", "0", "no"):
            with env(FLAG=value):
                self.assertFalse(_bool_from_env("FLAG"))
        with env(FLAG="maybe"), self.assertRaisesRegex(ValueError, "boolean"):
            _bool_from_env("FLAG")

    def test_jwt_settings_defaults_and_invalid_values(self):
        secret = "s" * 32
        with env(JWT_SECRET_KEY=secret):
            settings = JWTSettings.from_env()
        self.assertEqual(settings.access_token_expire_minutes, 15)
        self.assertEqual(settings.refresh_token_expire_days, 7)
        with env(JWT_SECRET_KEY=secret, JWT_ALGORITHM="RS256"):
            with self.assertRaisesRegex(ValueError, "JWT_ALGORITHM"):
                JWTSettings.from_env()
        with env(JWT_SECRET_KEY=secret, JWT_ACCESS_TOKEN_EXPIRE_MINUTES="0"):
            with self.assertRaisesRegex(ValueError, "ACCESS_TOKEN"):
                JWTSettings.from_env()

    def test_email_settings_defaults(self):
        with env():
            settings = EmailSettings.from_env()
        self.assertEqual(settings.smtp_host, "localhost")
        self.assertEqual(settings.smtp_port, 1025)
        self.assertIsNone(settings.smtp_username)
        self.assertIsNone(settings.smtp_password)
        self.assertFalse(settings.use_starttls)
        self.assertEqual(settings.app_base_url, "http://localhost:8000")

    def test_email_settings_from_environment(self):
        with env(
            SMTP_HOST="smtp.test",
            SMTP_PORT="587",
            SMTP_USERNAME="user",
            SMTP_PASSWORD="pw",
            SMTP_FROM_EMAIL="from@test",
            SMTP_USE_STARTTLS="true",
            APP_BASE_URL="https://app.test/",
        ):
            settings = EmailSettings.from_env()
        self.assertEqual(
            (
                settings.smtp_host,
                settings.smtp_port,
                settings.smtp_username,
                settings.smtp_password,
                settings.from_email,
                settings.use_starttls,
                settings.app_base_url,
            ),
            ("smtp.test", 587, "user", "pw", "from@test", True, "https://app.test"),
        )

    def test_email_settings_reject_invalid_port_and_flag(self):
        with env(SMTP_PORT="0"), self.assertRaisesRegex(ValueError, "SMTP_PORT"):
            EmailSettings.from_env()
        with env(SMTP_USE_STARTTLS="x"), self.assertRaises(ValueError):
            EmailSettings.from_env()

    def test_cors_settings(self):
        with env():
            self.assertEqual(CORSSettings.from_env().allowed_origins, ())
        with env(ALLOWED_ORIGINS=" https://a.test, ,https://b.test "):
            self.assertEqual(
                CORSSettings.from_env().allowed_origins,
                ("https://a.test", "https://b.test"),
            )

    def test_redis_settings(self):
        with env():
            settings = RedisSettings.from_env()
        self.assertEqual(
            (settings.host, settings.port, settings.database, settings.password),
            ("localhost", 6379, 0, None),
        )
        self.assertEqual(settings.cache_ttl_seconds, 900)
        with env(
            REDIS_HOST="r",
            REDIS_PORT="1",
            REDIS_DB="2",
            REDIS_PASSWORD="p",
            REDIS_CACHE_TTL_SECONDS="30",
        ):
            settings = RedisSettings.from_env()
        self.assertEqual(
            (settings.host, settings.port, settings.database, settings.password),
            ("r", 1, 2, "p"),
        )
        self.assertEqual(settings.cache_ttl_seconds, 30)
        with env(REDIS_PORT="-1"), self.assertRaises(ValueError):
            RedisSettings.from_env()

    def test_cloudinary_settings(self):
        with env(
            CLOUDINARY_CLOUD_NAME=" cloud ",
            CLOUDINARY_API_KEY="key",
            CLOUDINARY_API_SECRET="secret",
        ):
            settings = CloudinarySettings.from_env()
        self.assertEqual(
            (settings.cloud_name, settings.api_key, settings.api_secret),
            ("cloud", "key", "secret"),
        )
        with env(CLOUDINARY_CLOUD_NAME="cloud", CLOUDINARY_API_KEY="  "):
            with self.assertRaisesRegex(
                ValueError, "CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET"
            ):
                CloudinarySettings.from_env()


class MailerTests(unittest.TestCase):
    def send(self, **environment):
        smtp = MagicMock()
        smtp_class = MagicMock()
        smtp_class.return_value.__enter__.return_value = smtp
        with env(**environment), patch.object(mailer.smtplib, "SMTP", smtp_class):
            mailer._send_email("to@example.com", "Subject", "Body")
        return smtp_class, smtp

    def test_plain_smtp_without_tls_or_login(self):
        smtp_class, smtp = self.send()

        smtp_class.assert_called_once_with("localhost", 1025, timeout=10)
        smtp.starttls.assert_not_called()
        smtp.login.assert_not_called()
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "to@example.com")
        self.assertEqual(message["From"], "no-reply@example.com")
        self.assertEqual(message["Subject"], "Subject")
        self.assertEqual(message.get_content().strip(), "Body")

    def test_starttls_and_login(self):
        smtp_class, smtp = self.send(
            SMTP_HOST="smtp.test",
            SMTP_PORT="587",
            SMTP_USE_STARTTLS="true",
            SMTP_USERNAME="user",
            SMTP_PASSWORD="pw",
        )

        smtp_class.assert_called_once_with("smtp.test", 587, timeout=10)
        smtp.starttls.assert_called_once_with()
        smtp.login.assert_called_once_with("user", "pw")
        smtp.send_message.assert_called_once()

    def test_login_with_username_but_no_password_uses_empty_string(self):
        _, smtp = self.send(SMTP_USERNAME="user")

        smtp.login.assert_called_once_with("user", "")

    def test_verification_email_contains_link(self):
        with (
            env(APP_BASE_URL="https://app.test/"),
            patch.object(mailer, "_send_email") as send,
        ):
            mailer.send_verification_email("a@example.com", "tok")

        recipient, subject, body = send.call_args.args
        self.assertEqual(recipient, "a@example.com")
        self.assertIn("Verify", subject)
        self.assertIn("https://app.test/auth/verify-email?token=tok", body)

    def test_password_reset_email_contains_token(self):
        with env(), patch.object(mailer, "_send_email") as send:
            mailer.send_password_reset_email("a@example.com", "tok")

        recipient, subject, body = send.call_args.args
        self.assertEqual(recipient, "a@example.com")
        self.assertIn("Reset", subject)
        self.assertIn("tok", body)
        self.assertIn("http://localhost:8000/auth/reset-password", body)


class UserCacheTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        patcher = patch.object(user_cache, "_client", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_client_uses_redis_settings(self):
        with (
            env(REDIS_HOST="r", REDIS_PORT="7", REDIS_DB="3", REDIS_PASSWORD="p"),
            patch.object(user_cache.redis, "Redis") as redis_class,
        ):
            _REAL_CLIENT()

        kwargs = redis_class.call_args.kwargs
        self.assertEqual(kwargs["host"], "r")
        self.assertEqual(kwargs["port"], 7)
        self.assertEqual(kwargs["db"], 3)
        self.assertEqual(kwargs["password"], "p")
        self.assertTrue(kwargs["decode_responses"])

    def test_get_cached_user_hit(self):
        self.client.get.return_value = json.dumps(
            {
                "id": 1,
                "email": "a@example.com",
                "is_email_verified": True,
                "avatar_url": "https://img",
            }
        )

        user = user_cache.get_cached_user(1)

        self.client.get.assert_called_once_with("user:1")
        self.assertEqual(
            (user.id, user.email, user.is_email_verified, user.avatar_url),
            (1, "a@example.com", True, "https://img"),
        )

    def test_get_cached_user_miss(self):
        self.client.get.return_value = None

        self.assertIsNone(user_cache.get_cached_user(1))
        self.client.delete.assert_not_called()

    def test_get_cached_user_redis_error_returns_none(self):
        self.client.get.side_effect = redis.RedisError("down")

        with self.assertLogs(user_cache.logger, "WARNING"):
            self.assertIsNone(user_cache.get_cached_user(1))

    def test_malformed_cache_entries_are_discarded(self):
        entries = [
            "not json",
            "[]",
            json.dumps({"id": 2, "email": "a@example.com", "is_email_verified": True}),
            json.dumps({"id": 1, "email": 5, "is_email_verified": True}),
            json.dumps({"id": 1, "email": "a@example.com", "is_email_verified": 1}),
            json.dumps({"id": 1}),
        ]
        for entry in entries:
            with self.subTest(entry=entry):
                self.client.reset_mock()
                self.client.get.return_value = entry

                with self.assertLogs(user_cache.logger, "WARNING"):
                    self.assertIsNone(user_cache.get_cached_user(1))
                self.client.delete.assert_called_once_with("user:1")

    def test_cache_user_writes_with_ttl(self):
        user = User(id=4, email="a@example.com", is_email_verified=True)
        with env(REDIS_CACHE_TTL_SECONDS="60"):
            user_cache.cache_user(user)

        key, payload = self.client.set.call_args.args
        self.assertEqual(key, "user:4")
        self.assertEqual(json.loads(payload)["email"], "a@example.com")
        self.assertIsNone(json.loads(payload)["avatar_url"])
        self.assertEqual(self.client.set.call_args.kwargs, {"ex": 60})

    def test_cache_user_swallows_redis_error(self):
        self.client.set.side_effect = redis.RedisError("down")

        with env(), self.assertLogs(user_cache.logger, "WARNING"):
            user_cache.cache_user(User(id=1, email="a@example.com"))

    def test_invalidate_deletes_key_and_swallows_error(self):
        user_cache.invalidate_user_cache(9)
        self.client.delete.assert_called_once_with("user:9")

        self.client.delete.side_effect = redis.RedisError("down")
        with self.assertLogs(user_cache.logger, "WARNING"):
            user_cache.invalidate_user_cache(9)



class RateLimiterTests(unittest.TestCase):
    def test_blocks_at_limit_and_sets_retry_after(self):
        limiter = rate_limit.InMemoryRateLimiter()
        with patch.object(rate_limit, "monotonic", side_effect=[0.0, 10.0, 20.0]):
            limiter.check("k", 2, 60)
            limiter.check("k", 2, 60)
            with self.assertRaises(HTTPException) as error:
                limiter.check("k", 2, 60)

        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(error.exception.headers["Retry-After"], "40")

    def test_retry_after_is_at_least_one_second(self):
        limiter = rate_limit.InMemoryRateLimiter()
        with patch.object(rate_limit, "monotonic", side_effect=[0.0, 59.9]):
            limiter.check("k", 1, 60)
            with self.assertRaises(HTTPException) as error:
                limiter.check("k", 1, 60)

        self.assertEqual(error.exception.headers["Retry-After"], "1")

    def test_old_requests_at_cutoff_are_evicted(self):
        limiter = rate_limit.InMemoryRateLimiter()
        with patch.object(rate_limit, "monotonic", side_effect=[0.0, 60.0, 60.0]):
            limiter.check("k", 1, 60)
            limiter.check("k", 1, 60)
            with self.assertRaises(HTTPException):
                limiter.check("k", 1, 60)

    def test_keys_are_independent(self):
        limiter = rate_limit.InMemoryRateLimiter()
        limiter.check("a", 1, 60)
        limiter.check("b", 1, 60)
        with self.assertRaises(HTTPException):
            limiter.check("a", 1, 60)


class RateLimitDependencyTests(unittest.TestCase):
    def request(self, route=None):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/contacts/5",
            "headers": [],
            "query_string": b"",
        }
        if route is not None:
            scope["route"] = route
        return Request(scope)

    def test_request_limit_uses_route_template_when_available(self):
        user = SimpleNamespace(id=3)
        with (
            env(CONTACT_REQUESTS_PER_MINUTE="7"),
            patch.object(rate_limit.contact_limiter, "check") as check,
        ):
            rate_limit.limit_contact_requests(
                self.request(SimpleNamespace(path="/contacts/{contact_id}")), user
            )
            rate_limit.limit_contact_requests(self.request(), user)

        self.assertEqual(
            [call.args for call in check.call_args_list],
            [
                ("3:GET:/contacts/{contact_id}", 7, 60),
                ("3:GET:/contacts/5", 7, 60),
            ],
        )

    def test_creation_limit_uses_configured_value(self):
        with (
            env(CONTACT_CREATES_PER_MINUTE="2"),
            patch.object(rate_limit.create_contact_limiter, "check") as check,
        ):
            rate_limit.limit_contact_creation(SimpleNamespace(id=8))

        check.assert_called_once_with("user:8", 2, 60)

    def test_invalid_limit_configuration_is_rejected(self):
        with env(CONTACT_REQUESTS_PER_MINUTE="zero"):
            with self.assertRaises(ValueError):
                rate_limit.limit_contact_requests(
                    self.request(), SimpleNamespace(id=1)
                )


class CloudinaryServiceTests(unittest.TestCase):
    cloud_env = {
        "CLOUDINARY_CLOUD_NAME": "cloud",
        "CLOUDINARY_API_KEY": "key",
        "CLOUDINARY_API_SECRET": "secret",
    }

    def test_rejects_unsupported_content_types(self):
        for content_type in (None, "image/gif", "text/plain"):
            with self.assertRaises(cloudinary_service.InvalidAvatarError):
                cloudinary_service.upload_avatar(BytesIO(b"x"), content_type)

    def test_rejects_empty_and_oversized_files(self):
        sizes = (0, cloudinary_service.MAX_AVATAR_SIZE_BYTES + 1)
        for size in sizes:
            with self.subTest(size=size):
                with self.assertRaisesRegex(
                    cloudinary_service.InvalidAvatarError, "5 MB"
                ):
                    cloudinary_service.upload_avatar(
                        BytesIO(b"x" * size), "image/png"
                    )

    def test_accepts_file_at_size_limit(self):
        file = BytesIO(b"x" * cloudinary_service.MAX_AVATAR_SIZE_BYTES)
        with (
            env(**self.cloud_env),
            patch.object(
                cloudinary_service.cloudinary.uploader,
                "upload",
                return_value={"secure_url": "https://cdn/a.png"},
            ) as upload,
        ):
            url = cloudinary_service.upload_avatar(file, "image/png")

        self.assertEqual(url, "https://cdn/a.png")
        self.assertEqual(file.tell(), 0)
        self.assertEqual(upload.call_args.kwargs["folder"], "avatars")
        self.assertEqual(upload.call_args.kwargs["api_secret"], "secret")

    def test_missing_cloudinary_settings_do_not_call_service(self):
        with (
            env(),
            patch.object(cloudinary_service.cloudinary.uploader, "upload") as upload,
        ):
            with self.assertRaisesRegex(ValueError, "Missing Cloudinary"):
                cloudinary_service.upload_avatar(BytesIO(b"x"), "image/webp")

        upload.assert_not_called()

    def test_non_secure_or_missing_url_is_an_error(self):
        for result in ({}, {"secure_url": "http://cdn/a.png"}, {"secure_url": 5}):
            with self.subTest(result=result):
                with (
                    env(**self.cloud_env),
                    patch.object(
                        cloudinary_service.cloudinary.uploader,
                        "upload",
                        return_value=result,
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, "secure avatar URL"):
                        cloudinary_service.upload_avatar(BytesIO(b"x"), "image/jpeg")


if __name__ == "__main__":
    unittest.main()
