"""Track 1: predict future days using live SMHI weather and staged Neon prices.

Copy this file into the course repository as api/main_realtime.py and run:
    HF_REVISION=v1 uv run uvicorn api.main_realtime:app --reload
    curl localhost:8000/health
    curl localhost:8000/predict

Requires HF_MODEL_REPO, DATABASE_URL and optionally PRICE_AREA (default SE3),
HF_TOKEN (private models). Requires pred__log from prediction_log/pred_log.sql.
No feat__daily or stg__forecast is used.
"""

from __future__ import annotations

import os
import pickle
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg2
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from huggingface_hub import hf_hub_download
from psycopg2.extras import Json

load_dotenv()

STOCKHOLM = ZoneInfo("Europe/Stockholm")
PRICE_AREA = os.getenv("PRICE_AREA", "SE3").upper()
HF_REVISION = os.getenv("HF_REVISION", "main")
AREA_COORDS = {
    "SE1": (65.5435, 22.1219),
    "SE2": (63.1792, 14.6357),
    "SE3": (59.3417, 18.0549),
}
SMHI_FORECAST_URL = (
    "https://opendata-download-metfcst.smhi.se/api/category/snow1g/version/1"
    "/geotype/point/lon/{lon}/lat/{lat}/data.json"
    "?parameters=air_temperature,wind_speed"
)
EXPECTED_FEATURES = {
    "tomorrow_temp_mean",
    "tomorrow_temp_min",
    "tomorrow_temp_max",
    "tomorrow_wind_mean",
    "price_today",
    "price_yesterday",
    "price_7d_ago",
    "price_7d_mean",
    "peak_today",
    "dayofweek",
    "month",
    "is_weekend",
}

PAYLOAD: dict = {}
LOAD_ERROR: str | None = None


def load_payload() -> dict:
    path = hf_hub_download(
        repo_id=os.environ["HF_MODEL_REPO"],
        filename="model.pkl",
        revision=HF_REVISION,
        token=os.getenv("HF_TOKEN") or None,
    )
    with open(path, "rb") as fh:
        payload = pickle.load(fh)
    if set(payload["features"]) != EXPECTED_FEATURES:
        raise ValueError("Model feature list differs from the Track 1 feature builder")
    if payload.get("price_area") != PRICE_AREA:
        raise ValueError(f"Model price_area {payload.get('price_area')} != {PRICE_AREA}")
    return payload


@asynccontextmanager
async def lifespan(app: FastAPI):
    global LOAD_ERROR
    try:
        PAYLOAD.update(load_payload())
        LOAD_ERROR = None
        print(f"loaded model from {os.getenv('HF_MODEL_REPO')} @ {HF_REVISION}")
    except Exception as exc:  # noqa: BLE001 - keep /health available after load failure
        LOAD_ERROR = f"{type(exc).__name__}: {exc}"
        print(f"could not load model: {LOAD_ERROR}")
    yield


app = FastAPI(title="Elprisprediktion (direktprognos)", lifespan=lifespan)


def expected_price_instants(day: date) -> set[datetime]:
    """Quarter-hour slots between local midnights, including DST's 92/100 days."""
    start = datetime.combine(day, datetime.min.time(), STOCKHOLM).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), STOCKHOLM).astimezone(UTC)
    slots = set()
    while start < end:
        slots.add(start)
        start += timedelta(minutes=15)
    return slots


def daily_prices(anchor: date) -> tuple[dict[date, dict], int]:
    """Aggregate by Swedish local day; keep distinct instants, not a fixed 24 rows."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT starts_at, sek_per_kwh
                FROM stg__elpris
                WHERE price_area = %s
                  AND starts_at >= %s
                  AND starts_at < %s
                ORDER BY starts_at
                """,
                (
                    PRICE_AREA,
                    datetime.combine(anchor - timedelta(days=7), datetime.min.time(), STOCKHOLM),
                    datetime.combine(anchor + timedelta(days=1), datetime.min.time(), STOCKHOLM),
                ),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    by_day: dict[date, dict[datetime, float]] = {}
    for instant, value in rows:
        if instant.tzinfo is None or value is None:
            raise ValueError("Price view returned an invalid timestamp or NULL price")
        local_day = instant.astimezone(STOCKHOLM).date()
        by_day.setdefault(local_day, {})[instant.astimezone(UTC)] = float(value)
    daily = {
        day: {"mean": mean(instants.values()), "peak": max(instants.values()),
              "intervals": len(instants),
              "expected_intervals": len(expected_price_instants(day)),
              "complete": set(instants) == expected_price_instants(day)}
        for day, instants in by_day.items()
    }
    return daily, sum(item["intervals"] for item in daily.values())


def forecast_weather(target: date) -> tuple[dict, int]:
    """Aggregate forecast values whose valid times fall on target's Swedish day."""
    if PRICE_AREA not in AREA_COORDS:
        raise ValueError(f"No SMHI forecast coordinate for {PRICE_AREA}")
    lat, lon = AREA_COORDS[PRICE_AREA]
    response = requests.get(SMHI_FORECAST_URL.format(lat=lat, lon=lon), timeout=15)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("timeSeries"), list):
        raise TypeError("SMHI forecast payload has no timeSeries")
    temps: list[float] = []
    winds: list[float] = []
    for point in payload["timeSeries"]:
        stamp = datetime.fromisoformat(point["time"].replace("Z", "+00:00"))
        if stamp.astimezone(STOCKHOLM).date() != target:
            continue
        # snow1g returns selected fields in point["data"], not a parameters list.
        values = point.get("data", {})
        temp = values.get("air_temperature")
        wind = values.get("wind_speed")
        if temp is not None:
            temps.append(float(temp))
        if wind is not None:
            winds.append(float(wind))
    if not temps or not winds:
        raise ValueError(f"SMHI forecast has no temperature or wind for {target}")
    return {
        "tomorrow_temp_mean": mean(temps),
        "tomorrow_temp_min": min(temps),
        "tomorrow_temp_max": max(temps),
        "tomorrow_wind_mean": mean(winds),
    }, min(len(temps), len(winds))


def build_live_features(target: date, prices: dict[date, dict], weather: dict) -> dict:
    anchor = target - timedelta(days=1)
    required_days = (anchor, anchor - timedelta(days=1), anchor - timedelta(days=7))
    missing = [d.isoformat() for d in required_days if d not in prices]
    if missing:
        raise ValueError(f"Missing stg__elpris prices for {PRICE_AREA} on {', '.join(missing)}")
    if not prices[anchor]["complete"]:
        raise ValueError(
            f"Incomplete 15-minute prices for {PRICE_AREA} on {anchor}: "
            f"{prices[anchor]['intervals']}/{prices[anchor]['expected_intervals']} slots"
        )
    window = [prices[anchor - timedelta(days=i)]["mean"] for i in range(7)
              if anchor - timedelta(days=i) in prices]
    if len(window) < 3:
        raise ValueError("Fewer than 3 price days in the 7-day rolling mean")
    return {
        **weather,
        "price_today": prices[anchor]["mean"],
        "price_yesterday": prices[anchor - timedelta(days=1)]["mean"],
        "price_7d_ago": prices[anchor - timedelta(days=7)]["mean"],
        "price_7d_mean": mean(window),
        "peak_today": prices[anchor]["peak"],
        # Training features are for day D, not the predicted day D+1.
        "dayofweek": anchor.weekday(),
        "month": anchor.month,
        "is_weekend": int(anchor.weekday() >= 5),
    }


def log_prediction(target: date, prediction: float, inputs: pd.DataFrame) -> int:
    """Persist the exact numeric row passed to model.predict before serving it."""
    snapshot = {name: float(inputs.iloc[0][name]) for name in inputs.columns}
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pred__log (
                    price_area, target_date, y_hat, model_repo, model_revision,
                    feature_built_at, features
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING prediction_id
                """,
                (
                    PRICE_AREA, target, prediction, os.environ["HF_MODEL_REPO"],
                    HF_REVISION, None, Json(snapshot),
                ),
            )
            prediction_id = cur.fetchone()[0]
        conn.commit()
        return prediction_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model_loaded": bool(PAYLOAD),
        "load_error": LOAD_ERROR,
        "model_repo": os.getenv("HF_MODEL_REPO"),
        "revision": HF_REVISION,
        "price_area": PRICE_AREA,
        "forecast_source": "SMHI live",
        "price_source": "Neon stg__elpris",
    }


@app.get("/predict")
def predict(target_date: date | None = None) -> dict:
    """Default to tomorrow; later dates need complete preceding-day prices."""
    if not PAYLOAD:
        raise HTTPException(503, f"Model not loaded: {LOAD_ERROR}")
    tomorrow = datetime.now(STOCKHOLM).date() + timedelta(days=1)
    target = target_date or tomorrow
    if target < tomorrow:
        raise HTTPException(422, f"Track 1 supports future dates starting {tomorrow}.")
    try:
        prices, _ = daily_prices(target - timedelta(days=1))
        anchor = target - timedelta(days=1)
        if anchor not in prices:
            raise ValueError(f"Missing stg__elpris prices for {PRICE_AREA} on {anchor}")
        if not prices[anchor]["complete"]:
            raise ValueError(
                f"Incomplete 15-minute prices for {PRICE_AREA} on {anchor}: "
                f"{prices[anchor]['intervals']}/{prices[anchor]['expected_intervals']} slots"
            )
        weather, weather_points = forecast_weather(target)
        values = build_live_features(target, prices, weather)
        ordered = PAYLOAD["features"]
        X = pd.DataFrame([{name: values[name] for name in ordered}]).astype(float)
        prediction = float(PAYLOAD["model"].predict(X)[0])
    except (psycopg2.Error, requests.RequestException, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(503, f"Prediction inputs unavailable: {exc}") from exc
    try:
        prediction_id = log_prediction(target, prediction, X)
    except (psycopg2.Error, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(503, f"Prediction logging failed: {exc}") from exc
    anchor = target - timedelta(days=1)
    return {
        "prediction_id": prediction_id,
        "price_area": PRICE_AREA,
        "target_date": target,
        "target": PAYLOAD["target"],
        "prediction_sek_per_kwh": prediction,
        "model_revision": HF_REVISION,
        "weather_source": "SMHI live forecast",
        "weather_points": weather_points,
        "price_source": "Neon stg__elpris",
        "price_anchor_date": anchor,
        "price_intervals_today": prices[anchor]["intervals"],
        "price_intervals_expected": prices[anchor]["expected_intervals"],
        "price_anchor_is_current_day": anchor == datetime.now(STOCKHOLM).date(),
    }
