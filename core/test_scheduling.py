from datetime import datetime, time, timezone as datetime_timezone

from django.test import SimpleTestCase

from core.scheduling import scheduler_time_is_due


class ConfigurableSchedulerTimeTests(SimpleTestCase):
    def test_uses_pakistan_time_and_five_minute_window(self):
        configured = time(9, 0)

        self.assertTrue(scheduler_time_is_due(
            configured,
            now=datetime(2026, 8, 16, 4, 0, tzinfo=datetime_timezone.utc),
        ))
        self.assertTrue(scheduler_time_is_due(
            configured,
            now=datetime(2026, 8, 16, 4, 4, 59, tzinfo=datetime_timezone.utc),
        ))
        self.assertFalse(scheduler_time_is_due(
            configured,
            now=datetime(2026, 8, 16, 4, 5, tzinfo=datetime_timezone.utc),
        ))

