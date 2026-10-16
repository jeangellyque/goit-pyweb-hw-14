import os
import unittest
from unittest.mock import patch

from app.config import JWTSettings


class JWTSettingsTests(unittest.TestCase):
    def setUp(self):
        self.environment = {
            "JWT_SECRET_KEY": "a-random-secret-key-with-at-least-32-bytes",
            "JWT_ALGORITHM": "HS256",
            "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
            "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        }

    def test_loads_jwt_settings_from_environment(self):
        with patch.dict(os.environ, self.environment, clear=True):
            settings = JWTSettings.from_env()

        self.assertEqual(settings.secret_key, self.environment["JWT_SECRET_KEY"])
        self.assertEqual(settings.algorithm, "HS256")
        self.assertEqual(settings.access_token_expire_minutes, 15)
        self.assertEqual(settings.refresh_token_expire_days, 7)

    def test_rejects_short_secret(self):
        with patch.dict(
            os.environ,
            {**self.environment, "JWT_SECRET_KEY": "too-short"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "JWT_SECRET_KEY"):
                JWTSettings.from_env()

    def test_rejects_unsupported_algorithm(self):
        with patch.dict(
            os.environ,
            {**self.environment, "JWT_ALGORITHM": "none"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "JWT_ALGORITHM"):
                JWTSettings.from_env()

    def test_rejects_non_positive_token_lifetimes(self):
        with patch.dict(
            os.environ,
            {
                **self.environment,
                "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "0",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "JWT_ACCESS_TOKEN_EXPIRE_MINUTES"):
                JWTSettings.from_env()


if __name__ == "__main__":
    unittest.main()
