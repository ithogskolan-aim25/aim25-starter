"""
Apply feat_table_reference.sql before running this file.
This is a deliberately small first version: it builds the historical table
from stg__ views and can be run again without creating duplicate rows.
"""

from __future__ import annotations

import os

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

from model.train import (
    AREA_STATIONS,
    STOCKHOLM,
    build_features,
    daily_price_table,
    daily_weather_table,
)

load_dotenv()

PRICE_AREA = os.environ["PRICE_AREA"]
PARAM_TEMP = "1"
PARAM_WIND = "4"


def load_prices_db(conn, area: str) -> pd.DataFrame:
    """Read staged price intervals in the shape daily_price_table() expects."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT starts_at, sek_per_kwh
            FROM stg__elpris
            WHERE price_area = %s
            ORDER BY starts_at
            """,
            (area,),
        )
        rows = cur.fetchall()

    if not rows:
        raise ValueError(f"No price rows in stg__elpris for {area}.")

    df = pd.DataFrame(rows, columns=["starts_at", "SEK_per_kWh"])
    df["ts_utc"] = pd.to_datetime(df["starts_at"], utc=True)
    df["ts_local"] = df["ts_utc"].dt.tz_convert(STOCKHOLM)
    df["date_local"] = df["ts_local"].dt.date
    return df.sort_values("ts_utc").reset_index(drop=True)


def load_weather_db(conn, station: int) -> pd.DataFrame:
    """Read long-form weather data and reshape it for daily_weather_table()."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT parameter, observed_at, value
            FROM stg__weather
            WHERE station = %s AND parameter IN (%s, %s)
            ORDER BY observed_at
            """,
            (str(station), PARAM_TEMP, PARAM_WIND),
        )
        rows = cur.fetchall()

    if not rows:
        raise ValueError(f"No weather rows in stg__weather for station {station}.")

    long_df = pd.DataFrame(rows, columns=["parameter", "observed_at", "value"])
    wide = long_df.pivot(index="observed_at", columns="parameter", values="value")
    wide = wide.rename(columns={PARAM_TEMP: "temp_c", PARAM_WIND: "wind_ms"})

    missing = {"temp_c", "wind_ms"} - set(wide.columns)
    if missing:
        raise ValueError(f"Weather data is missing parameter(s): {sorted(missing)}")

    weather = wide.reset_index()
    weather["ts_utc"] = pd.to_datetime(weather["observed_at"], utc=True)
    weather["ts_local"] = weather["ts_utc"].dt.tz_convert(STOCKHOLM)
    weather["date_local"] = weather["ts_local"].dt.date
    return weather.sort_values("ts_utc").reset_index(drop=True)


def write_features(conn, area: str, features: pd.DataFrame) -> int:
    """Write one feature row per target date, updating rows on a second run."""
    if features.empty:
        return 0

    sql = """
        INSERT INTO feat__daily (
            price_area,
            target_date,
            tomorrow_temp_mean,
            tomorrow_temp_min,
            tomorrow_temp_max,
            tomorrow_wind_mean,
            price_today,
            price_yesterday,
            price_7d_ago,
            price_7d_mean,
            peak_today,
            dayofweek,
            month,
            is_weekend,
            y
        ) VALUES %s
        ON CONFLICT (price_area, target_date) DO UPDATE SET
            tomorrow_temp_mean = EXCLUDED.tomorrow_temp_mean,
            tomorrow_temp_min = EXCLUDED.tomorrow_temp_min,
            tomorrow_temp_max = EXCLUDED.tomorrow_temp_max,
            tomorrow_wind_mean = EXCLUDED.tomorrow_wind_mean,
            price_today = EXCLUDED.price_today,
            price_yesterday = EXCLUDED.price_yesterday,
            price_7d_ago = EXCLUDED.price_7d_ago,
            price_7d_mean = EXCLUDED.price_7d_mean,
            peak_today = EXCLUDED.peak_today,
            dayofweek = EXCLUDED.dayofweek,
            month = EXCLUDED.month,
            is_weekend = EXCLUDED.is_weekend,
            y = EXCLUDED.y,
            built_at = now()
    """

    rows = []
    for feature_date, row in features.iterrows():
        # build_features() describes information available on D and predicts D+1.
        target_date = (pd.Timestamp(feature_date) + pd.Timedelta(days=1)).date()
        rows.append(
            (
                area,
                target_date,
                row["tomorrow_temp_mean"],
                row["tomorrow_temp_min"],
                row["tomorrow_temp_max"],
                row["tomorrow_wind_mean"],
                row["price_today"],
                row["price_yesterday"],
                row["price_7d_ago"],
                row["price_7d_mean"],
                row["peak_today"],
                row["dayofweek"],
                int(row["month"]),
                bool(row["is_weekend"]),
                row["y"],
            )
        )

    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
    return len(rows)


def refresh_daily_features(conn, area: str) -> int:
    """Build the team's feature table once from the staging views."""
    station, _ = AREA_STATIONS[area]
    prices = load_prices_db(conn, area)
    weather = load_weather_db(conn, station)

    daily_prices = daily_price_table(prices)
    daily_weather = daily_weather_table(weather)
    features = build_features(daily_prices, daily_weather)

    return write_features(conn, area, features)


def main() -> None:
    with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
        n = refresh_daily_features(conn, PRICE_AREA)
    print(f"wrote {n} rows to feat__daily for {PRICE_AREA}")


if __name__ == "__main__":
    main()
