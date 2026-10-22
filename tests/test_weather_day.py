"""The weather for the day asked, at home unless a place is named (the field test, 2026-10-19: 'sunny tomorrow, 25'
was the reading of that moment, and 'current location' was looked up as a town in the USA)."""
from datetime import date

import pytest

from brain import agent, tools
from brain.tools import ToolFailure

DATA = {
    "current_condition": [{"weatherDesc": [{"value": "Sunny"}], "temp_C": "25", "FeelsLikeC": "26", "humidity": "40", "windspeedKmph": "10"}],
    "nearest_area": [{"areaName": [{"value": "Tripoli"}], "region": [{"value": "North"}], "country": [{"value": "Lebanon"}]}],
    "weather": [{"date": d, "mintempC": lo, "maxtempC": hi, "hourly": [{"chanceofrain": "0", "weatherDesc": [{"value": w}]}] * 8}
                for d, lo, hi, w in (("2026-10-19", "20", "26", "Sunny"), ("2026-10-20", "19", "24", "Light rain"), ("2026-10-21", "18", "23", "Cloudy"))],
}


class Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return DATA


@pytest.fixture
def wttr(monkeypatch):
    asked = []
    monkeypatch.setattr(tools.requests, "get", lambda url, **k: asked.append(url) or Resp())
    import datetime as dt

    class Today(dt.date):
        @classmethod
        def today(cls):
            return date(2026, 10, 19)
    monkeypatch.setattr(dt, "date", Today)
    return asked


def test_tomorrow_is_a_forecast_not_the_reading_now(wttr):
    out = tools.execute_weather_tool("Tripoli", "tomorrow")
    assert out.startswith("Forecast for Tripoli, Lebanon on Tue 20 Oct") and "Light rain" in out and "19 to 24" in out
    assert "Current" not in out


def test_now_is_the_current_reading(wttr):
    assert tools.execute_weather_tool("Tripoli", "now").startswith("Current weather in Tripoli, Lebanon: Sunny, 25")


def test_beyond_the_forecast_it_says_so(wttr):
    out = tools.execute_weather_tool("Tripoli", "2026-10-25")
    assert isinstance(out, ToolFailure) and "3 days ahead" in str(out)


def test_current_location_means_home(wttr, monkeypatch):
    from core import ui_settings
    monkeypatch.setattr(ui_settings, "get", lambda k, *a: "LEBANON,tripoli" if k == "home_location" else None)
    agent._weather({"location": "current location", "day": "tomorrow"}, None)
    assert "LEBANON,tripoli" in wttr[-1] and "current" not in wttr[-1].lower()
