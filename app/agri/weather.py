"""Weather, rainfall history and elevation from Open-Meteo.

Open-Meteo is free and needs no API key. It provides FAO reference
evapotranspiration (ET0), which is exactly what the irrigation calculation
needs. Every call has a timeout and one retry, and returns None on failure so
the calculators fall back to typical values and say so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.agri.zones import Place, find_state

logger = logging.getLogger("agri.weather")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class DayWeather:
    day: date
    et0_mm: float
    rain_mm: float
    rain_chance: int | None
    tmax_c: float | None


@dataclass(frozen=True)
class Forecast:
    today: DayWeather
    next_days: list[DayWeather]
    past_week_rain_mm: float
    source: str = "Open-Meteo"


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=3), reraise=True)
async def _get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


async def geocode(name: str) -> Place | None:
    """Find a Nigerian town, LGA or village by name."""
    try:
        data = await _get(GEOCODE_URL, {"name": name, "count": 5, "countryCode": "NG", "language": "en"})
    except Exception as error:  # noqa: BLE001
        logger.warning("Geocoding %r failed: %s", name, error)
        return None
    results = data.get("results") or []
    if not results:
        return None
    best = results[0]
    admin = best.get("admin1") or ""
    label = ", ".join(p for p in (best.get("name"), admin) if p)
    return Place(
        label=label,
        latitude=float(best["latitude"]),
        longitude=float(best["longitude"]),
        state=find_state(admin),
        exact=True,
    )


async def forecast(latitude: float, longitude: float) -> Forecast | None:
    """Today's ET0 and rain, the next two days, and last week's rain."""
    params = {
        "latitude": round(latitude, 4),
        "longitude": round(longitude, 4),
        "daily": "et0_fao_evapotranspiration,precipitation_sum,precipitation_probability_max,temperature_2m_max",
        "forecast_days": 3,
        "past_days": 7,
        "timezone": "Africa/Lagos",
    }
    try:
        data = await _get(FORECAST_URL, params)
        daily = data["daily"]
        days = [
            DayWeather(
                day=date.fromisoformat(daily["time"][i]),
                et0_mm=float(daily["et0_fao_evapotranspiration"][i] or 0.0),
                rain_mm=float(daily["precipitation_sum"][i] or 0.0),
                rain_chance=(daily.get("precipitation_probability_max") or [None] * 99)[i],
                tmax_c=(daily.get("temperature_2m_max") or [None] * 99)[i],
            )
            for i in range(len(daily["time"]))
        ]
    except Exception as error:  # noqa: BLE001
        logger.warning("Forecast failed: %s", error)
        return None
    past, upcoming = days[:7], days[7:]
    if not upcoming:
        return None
    return Forecast(
        today=upcoming[0],
        next_days=upcoming[1:],
        past_week_rain_mm=round(sum(d.rain_mm for d in past), 1),
    )


async def last_year_rainfall_mm(latitude: float, longitude: float, today: date | None = None) -> float | None:
    """Total rain over the last 365 days (ERA5 reanalysis via Open-Meteo)."""
    today = today or date.today()
    end = today - timedelta(days=6)  # archive lags a few days
    start = end - timedelta(days=364)
    try:
        data = await _get(
            ARCHIVE_URL,
            {
                "latitude": round(latitude, 4),
                "longitude": round(longitude, 4),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "daily": "precipitation_sum",
                "timezone": "Africa/Lagos",
            },
        )
        values = [v for v in data["daily"]["precipitation_sum"] if v is not None]
    except Exception as error:  # noqa: BLE001
        logger.warning("Rainfall history failed: %s", error)
        return None
    return round(sum(values), 0) if values else None


async def elevations(points: list[tuple[float, float]]) -> list[float] | None:
    """Ground height (m) for up to 100 points, from a 90 m terrain model."""
    if not points:
        return []
    try:
        data = await _get(
            ELEVATION_URL,
            {
                "latitude": ",".join(f"{p[0]:.5f}" for p in points[:100]),
                "longitude": ",".join(f"{p[1]:.5f}" for p in points[:100]),
            },
        )
        return [float(v) for v in data["elevation"]]
    except Exception as error:  # noqa: BLE001
        logger.warning("Elevation lookup failed: %s", error)
        return None


# Typical daily ET0 (mm) when no forecast is available, by zone and season.
# Rounded from FAO CLIMWAT-style ranges for Nigeria: the dry season in the
# north is hot and windy, the south is humid.
TYPICAL_ET0 = {
    ("sudan_sahel", "dry"): 7.0,
    ("sudan_sahel", "rainy"): 4.5,
    ("northern_guinea", "dry"): 6.0,
    ("northern_guinea", "rainy"): 4.0,
    ("southern_guinea", "dry"): 5.0,
    ("southern_guinea", "rainy"): 3.8,
    ("forest", "dry"): 4.0,
    ("forest", "rainy"): 3.3,
}
