"""
Minimal prediction service.

Two endpoints:

    GET /health    is the service up, and did the model load?
    GET /predict   predict tomorrow's price from the newest row in feat__daily

Run it locally:

    uv run uvicorn api.main:app --reload
    curl localhost:8000/health
    curl localhost:8000/predict

Configuration comes from the environment (see .env.example):

    HF_MODEL_REPO   the Hugging Face repo that holds model.pkl
    HF_REVISION     which version to load, e.g. v1   (default: main)
    HF_TOKEN        Hugging Face token, needed if the repo is private
    DATABASE_URL    your Postgres connection string
    PRICE_AREA      SE1 | SE2 | SE3 | SE4

Locally those come from .env. In Render you set the same names as
environment variables / secrets instead -- the code does not change.
"""

from __future__ import annotations

import os
import pickle
from contextlib import asynccontextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from huggingface_hub import hf_hub_download
from psycopg2.extras import RealDictCursor

load_dotenv()

HF_REVISION = os.getenv("HF_REVISION", "main")
PRICE_AREA = os.getenv("PRICE_AREA", "SE3").upper()

# Same zone train.py uses. Not date.today(): on Render the server runs in UTC,
# and between 22:00 and midnight Swedish time that is still yesterday's date.
STOCKHOLM = ZoneInfo("Europe/Stockholm")

# Filled in once at startup, by lifespan() below.
PAYLOAD: dict = {}
LOAD_ERROR: str | None = None


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------
def load_payload() -> dict:
    """Download model.pkl from the Hub and unpickle it.

    This is the exact payload train.py wrote and register_model.py uploaded:
    the fitted model plus the feature list, the target and the metrics it was
    registered with. The feature list is the important part -- it tells this
    service which columns to read, in which order, so the two halves cannot
    drift apart silently.
    """
    path = hf_hub_download(
        repo_id=os.environ["HF_MODEL_REPO"],
        filename="model.pkl",
        revision=HF_REVISION,
        token=os.getenv("HF_TOKEN") or None,
    )
    with open(path, "rb") as fh:
        return pickle.load(fh)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the model once at startup, not once per request."""
    global LOAD_ERROR
    try:
        PAYLOAD.update(load_payload())
        print(f"loaded model from {os.getenv('HF_MODEL_REPO')} @ {HF_REVISION}")
    except Exception as exc:
        # Deliberately not fatal: the service stays up so /health can tell
        # you what went wrong instead of the container just dying. A missing
        # env var shows up here as KeyError: 'HF_MODEL_REPO'.
        LOAD_ERROR = f"{type(exc).__name__}: {exc}"
        print(f"could not load model: {LOAD_ERROR}")
    yield


app = FastAPI(title="Elprisprediktion", lifespan=lifespan)


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------
def feature_row(target_date: date) -> dict | None:
    """The feat__daily row for one target date, for our price area.

    Asking for a named date rather than "ORDER BY target_date DESC LIMIT 1"
    is deliberate. The newest row in the table is always tomorrow, and the
    feature pipeline builds its tomorrow_* weather columns by shifting
    OBSERVED weather back one day -- so for tomorrow they are always NULL.
    Selecting the newest row hides that behind a prediction that silently
    runs on four missing features. Naming the date makes it visible, and
    lets you ask for a day that is fully populated.
    """
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT *
                FROM feat__daily
                WHERE price_area = %s AND target_date = %s
                """,
                (PRICE_AREA, target_date),
            )
            return cur.fetchone()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model_loaded": bool(PAYLOAD),
        "load_error": LOAD_ERROR,
        "model_repo": os.getenv("HF_MODEL_REPO"),
        "revision": HF_REVISION,
        "price_area": PRICE_AREA,
    }


@app.get("/predict")
def predict(target_date: date | None = None) -> dict:
    """Predict the price for one day. Defaults to today.

    Pass ?target_date=2026-09-22 to ask for another day -- tomorrow to get a
    real forecast, or any past day to check the model against an outcome it
    has already been graded on.
    """
    if not PAYLOAD:
        raise HTTPException(503, f"Model not loaded: {LOAD_ERROR}")

    if target_date is None:
        target_date = datetime.now(STOCKHOLM).date()

    row = feature_row(target_date)
    if row is None:
        raise HTTPException(404, f"No feat__daily row for {PRICE_AREA} on {target_date}.")

    # Same columns, same order as at training time. astype(float) turns
    # Postgres' booleans and smallints into the numbers the model expects,
    # and any NULL into the NaN the model was built to tolerate.
    features = PAYLOAD["features"]
    X = pd.DataFrame([{name: row[name] for name in features}]).astype(float)
    prediction = float(PAYLOAD["model"].predict(X)[0])

    return {
        "price_area": PRICE_AREA,
        "target_date": row["target_date"],
        "target": PAYLOAD["target"],
        "prediction_sek_per_kwh": prediction,
        # Which features were NULL in the database. Empty list = a full row.
        # Non-empty means the model guessed around a hole, and the number
        # above is worth less than it looks.
        "missing_features": [name for name in features if row[name] is None],
        # The real outcome, once it is known. NULL for a genuine forecast --
        # that is the point. Present here so you can compare the two without
        # a second query.
        "actual_sek_per_kwh": row["y"],
        "model_revision": HF_REVISION,
    }
