"""
Example implementation of the daily ingestion pipeline.

Env: DATABASE_URL, PRICE_AREA.
"""


import os
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

AREA_STATION = {
    "SE1": 162860,
    "SE2": 134110,
    "SE3": 97400,
}

AREA_COORDS = {
    "SE1": (65.5435, 22.1219),
    "SE2": (63.1792, 14.6357),
    "SE3": (59.3417, 18.0549),
}

PARAM_TEMP = "1"
PARAM_WIND = "4"

# New endpoint for forecast data. Needs to be added to the database schema.
SMHI_FORECAST_URL = (
    "https://opendata-download-metfcst.smhi.se/api/category/snow1g/version/1"
    "/geotype/point/lon/{lon}/lat/{lat}/data.json"
    "?parameters=air_temperature,wind_speed"
)

# --------------------------------------------------------------------------
# Ingest one day of prices.
# --------------------------------------------------------------------------
def ingest_prices(conn, area: str, day: date) -> int:
    url = f"https://www.elprisetjustnu.se/api/v1/prices/{day:%Y}/{day:%m-%d}_{area}.json"
    resp = requests.get(url, timeout=15)

    if resp.status_code == 404:
        # URL not found -- no prices for this date.
        return 0
    resp.raise_for_status()
    price_intervals = resp.json()

    # Question 2, "right shape": if the fields the insert depends on are
    # missing, raise an error for this day rather than inserting a row with missing data.
    if not price_intervals or "SEK_per_kWh" not in price_intervals[0]:
        raise ValueError(f"unexpected payload shape for {area} {day}: {price_intervals[:1]}")

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO raw__elpris (price_area, price_date, data) VALUES (%s, %s, %s)",
            (area, day, psycopg2.extras.Json(price_intervals)),
        )
    conn.commit()
    return len(price_intervals)


# --------------------------------------------------------------------------
# One day of weather: two parameters (temperature and wind speed)
# --------------------------------------------------------------------------
def ingest_weather(conn, area: str, day: date) -> int:
    '''
    Fetch the "latest months" of weather data for a given parameter and station.
    Then find the observations for the given day and insert them into the database.
    '''
    station = AREA_STATION[area]
    written = 0

    for param in (PARAM_TEMP, PARAM_WIND):
        url = (
            f"https://opendata-download-metobs.smhi.se/api/version/1.0/"
            f"parameter/{param}/station/{station}/period/latest-months/data.json"
        )
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            # Web page not found: no data for this parameter for this station.
            print(f"  parameter {param} 404 for station {station} -- skipping")
            continue
        resp.raise_for_status()
        payload = resp.json()

        # Data quality question 2 again.The returned payload should have a "value" array.
        if "value" not in payload:
            raise ValueError(f"unexpected weather payload shape: {list(payload)[:5]}")

        # SMHI's timestamps are epoch milliseconds, not ISO strings.
        from datetime import datetime, timezone
        rows = [
            v for v in payload["value"]
            if datetime.fromtimestamp(v["date"] / 1000, tz=timezone.utc).date() == day
        ]

        with conn.cursor() as cur:
            for v in rows:
                observed_at = datetime.fromtimestamp(v["date"] / 1000, tz=timezone.utc)
                cur.execute(
                    "INSERT INTO raw__weather (station, parameter, observed_at, data) "
                    "VALUES (%s, %s, %s, %s)",
                    (str(station), param, observed_at, psycopg2.extras.Json(v)),
                )
        conn.commit()
        written += len(rows)

    return written


def ingest_forecast(conn, area: str) -> int:
    now = datetime.now(timezone.utc)
    lat, lon = AREA_COORDS[area]
    response = requests.get(
        SMHI_FORECAST_URL.format(lat=lat, lon=lon), timeout=15
    )
    response.raise_for_status()

    rows = []
    for point in response.json()["timeSeries"]:
        valid_at = datetime.fromisoformat(point["time"].replace("Z", "+00:00"))
        if valid_at <= now:
            continue

        rows.append((
            f"forecast:{area}",
            "forecast",
            valid_at,
            psycopg2.extras.Json(point),
        ))

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO raw__weather (station, parameter, observed_at, data)
            VALUES %s
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def days_back(n: int) -> list[date]:
    '''
    Return a list of dates from today back to n days ago.
    '''
    today = date.today()
    return [today - timedelta(days=i) for i in range(n - 1, -1, -1)]


def main() -> None:
    load_dotenv()
    area = os.environ["PRICE_AREA"]
    conn = psycopg2.connect(os.environ["DATABASE_URL"])

    written = 0
    n_days = 7
    for day in days_back(n_days):
        n_prices = ingest_prices(conn, area, day)
        n_weather = ingest_weather(conn, area, day)  # metobs, historik
        written += n_prices + n_weather

    n_forecast = ingest_forecast(conn, area)  # metfcst, framtida tider
    written += n_forecast

    written += n_prices + n_weather

    n_forecast = ingest_forecast(conn, area)  # metfcst, framtida tider
    written += n_forecast

    conn.close()

    # Data quality question 1, "did it arrive?"
    if written == 0:
        raise SystemExit(f"ingested 0 rows for {n_days} days: check the logs for errors")

    print(f"ingested {written} rows over {n_days} days")


if __name__ == "__main__":
    main()
