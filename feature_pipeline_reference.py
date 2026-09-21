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

# How the daily ingest keys forecast rows in raw__weather. If your ingestion
# stores them under the SMHI station number instead, change this one line.
FORECAST_STATION = f"forecast:{PRICE_AREA}"


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


def load_forecast_db(conn, station: str) -> pd.DataFrame:
    """Daily aggregates of the weather FORECAST, one row per local date.

    Same shape as daily_weather_table(), so the two are interchangeable in
    the join below. As with the prices, no assumption is made about how many
    points a day has: the forecast is dense for the next few days and gets
    sparse further out, so aggregating over whatever exists is the only
    formula that stays correct.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT observed_at, temp_c, wind_ms
            FROM stg__forecast
            WHERE station = %s
            ORDER BY observed_at
            """,
            (station,),
        )
        rows = cur.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["valid_at", "temp_c", "wind_ms"])
    df[["temp_c", "wind_ms"]] = df[["temp_c", "wind_ms"]].astype(float)
    df["ts_local"] = pd.to_datetime(df["valid_at"], utc=True).dt.tz_convert(STOCKHOLM)
    df["date_local"] = df["ts_local"].dt.date

    grouped = df.groupby("date_local")
    daily = pd.DataFrame(
        {
            "temp_mean": grouped["temp_c"].mean(),
            "temp_min": grouped["temp_c"].min(),
            "temp_max": grouped["temp_c"].max(),
            "wind_mean": grouped["wind_ms"].mean(),
        }
    )
    daily.index = pd.to_datetime(daily.index)
    return daily.sort_index()


def fill_tomorrow_from_forecast(features: pd.DataFrame, forecast: pd.DataFrame) -> int:
    """Fill tomorrow_* where shifting the observations left a hole.

    build_features() derives tomorrow's weather by shifting OBSERVED weather
    back one day. For the newest row there is no next day to shift from, so
    those four columns are always NULL -- which is exactly the row you want
    to predict. This fills them from the forecast instead.

    Read this before trusting the result: the historical rows keep observed
    truth in tomorrow_*, and only the rows a forecast can reach get a
    forecast. The model is therefore trained on one thing and served
    another, and a forecast has error in it that the observation does not.
    That gap is real and it is not measured here. Quantifying it -- score
    the same days both ways once the forecast archive is deep enough -- is
    the honest next step, and the reason this function returns a count
    rather than filling silently.
    """
    if forecast.empty:
        return 0

    filled = 0
    for col in ("temp_mean", "temp_min", "temp_max", "wind_mean"):
        target = f"tomorrow_{col}"
        # Row D describes the day before target_date, so the weather wanted
        # for that row is the forecast valid on D + 1.
        wanted = forecast[col].reindex(features.index + pd.Timedelta(days=1))
        wanted.index = features.index

        gaps = features[target].isna()
        features.loc[gaps, target] = wanted[gaps]
        filled += int((gaps & wanted.notna()).sum())

    return filled


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

    forecast = load_forecast_db(conn, FORECAST_STATION)
    n_filled = fill_tomorrow_from_forecast(features, forecast)
    print(f"filled {n_filled} tomorrow_* values from the forecast")

    return write_features(conn, area, features)


def main() -> None:
    with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
        n = refresh_daily_features(conn, PRICE_AREA)
    print(f"wrote {n} rows to feat__daily for {PRICE_AREA}")


if __name__ == "__main__":
    main()
