"""Open-Meteo 429 handling: patient mode waits out the named hourly/daily window."""

from datetime import datetime, timezone

from powderbench import openmeteo


class _Resp:
    def __init__(self, reason):
        self._reason = reason

    def json(self):
        return {"error": True, "reason": self._reason}


def test_window_wait_hourly_and_daily():
    now = datetime(2026, 11, 20, 0, 40, 15, tzinfo=timezone.utc)
    hourly = openmeteo._window_wait(_Resp("Hourly API request limit exceeded. Please try again in the next hour."), now)
    assert hourly == 19 * 60 + 45 + 30  # to 01:00:00 UTC, plus 30 s
    daily = openmeteo._window_wait(_Resp("Daily API request limit exceeded. Please try again tomorrow."), now)
    assert daily == openmeteo.DAILY_PROBE_S  # probe hourly, not sleep to midnight
    late = datetime(2026, 11, 20, 23, 50, 0, tzinfo=timezone.utc)
    assert openmeteo._window_wait(_Resp("Daily API request limit exceeded."), late) == 10 * 60 + 60
    assert openmeteo._window_wait(_Resp("Minutely API request limit exceeded."), now) is None
    assert openmeteo._window_wait(None, now) is None
