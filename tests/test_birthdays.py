import unittest
from datetime import date

from app.main import upcoming_birthday_date


class UpcomingBirthdayTests(unittest.TestCase):
    def test_includes_today_and_last_day_of_seven_day_window(self):
        today = date(2025, 6, 10)
        self.assertEqual(upcoming_birthday_date(date(1990, 6, 10), today), today)
        self.assertEqual(
            upcoming_birthday_date(date(1990, 6, 16), today),
            date(2025, 6, 16),
        )

    def test_excludes_day_after_seven_day_window(self):
        self.assertIsNone(
            upcoming_birthday_date(date(1990, 6, 17), date(2025, 6, 10))
        )

    def test_handles_new_year_boundary(self):
        self.assertEqual(
            upcoming_birthday_date(date(1990, 1, 2), date(2024, 12, 30)),
            date(2025, 1, 2),
        )

    def test_leap_day_only_matches_a_leap_year_occurrence(self):
        self.assertIsNone(
            upcoming_birthday_date(date(2000, 2, 29), date(2025, 2, 27))
        )
        self.assertEqual(
            upcoming_birthday_date(date(2000, 2, 29), date(2024, 2, 27)),
            date(2024, 2, 29),
        )


if __name__ == "__main__":
    unittest.main()
