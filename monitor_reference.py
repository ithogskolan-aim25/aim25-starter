"""
Score yesterday's predictions against what the price actually turned out to be.

    uv run python monitor_reference.py

Add it to the daily workflow after the feature pipeline, and every morning
it tells you how the model did on the days that have now been settled.

Edge cases are deliberately not handled. A day with partial prices is
averaged over whatever intervals arrived, and a day with no qualifying
prediction simply does not appear.
"""

from __future__ import annotations

import os

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv()

SCORE_SQL = """
WITH chosen_predictions AS (
    SELECT DISTINCT ON (target_date)
        prediction_id, predicted_at, price_area, target_date,
        y_hat, model_revision
    FROM pred__log
    WHERE predicted_at < (target_date::timestamp AT TIME ZONE 'Europe/Stockholm')
      AND target_date < (now() AT TIME ZONE 'Europe/Stockholm')::date
    ORDER BY target_date, predicted_at DESC, prediction_id DESC
),
daily_prices AS (
    SELECT
        price_date,
        avg(sek_per_kwh) AS actual,
        count(*) AS n_intervals
    FROM stg__elpris
    WHERE price_date < (now() AT TIME ZONE 'Europe/Stockholm')::date
    GROUP BY price_date
),
scored AS (
    SELECT
        p.price_area, p.target_date, p.prediction_id, p.predicted_at,
        p.model_revision, p.y_hat, d.actual, d.n_intervals,
        -- The baseline to beat: "tomorrow will be like today", i.e. the day
        -- before the target. LEFT JOIN because the day before the first day
        -- in the table has no price, and a missing baseline is not a reason
        -- to drop an otherwise scorable day.
        prev.actual AS naive_y_hat,
        abs(p.y_hat - d.actual) AS abs_error
    FROM chosen_predictions p
    JOIN daily_prices d
      ON d.price_date = p.target_date
    LEFT JOIN daily_prices prev
      ON prev.price_date = p.target_date - 1
)
SELECT * FROM scored
ORDER BY target_date
"""


def score(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(SCORE_SQL)
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


STORE_SQL = """
    INSERT INTO monitor__daily (
        price_area, target_date, prediction_id, predicted_at,
        model_revision, y_hat, actual, naive_y_hat, n_intervals
    ) VALUES %s
    ON CONFLICT (price_area, target_date) DO UPDATE SET
        prediction_id  = EXCLUDED.prediction_id,
        predicted_at   = EXCLUDED.predicted_at,
        model_revision = EXCLUDED.model_revision,
        y_hat          = EXCLUDED.y_hat,
        actual         = EXCLUDED.actual,
        naive_y_hat    = EXCLUDED.naive_y_hat,
        n_intervals    = EXCLUDED.n_intervals,
        scored_at      = now()
"""


def store(conn, rows: list[dict]) -> int:
    """Write the grades to monitor__daily, one row per scored day.

    error, abs_error and naive_abs_error are not inserted: they are
    generated columns, so the arithmetic lives in the schema and cannot
    drift away from what this script believes it computed.
    """
    if not rows:
        return 0

    values = [
        (
            r["price_area"], r["target_date"], r["prediction_id"],
            r["predicted_at"], r["model_revision"], r["y_hat"],
            r["actual"], r["naive_y_hat"], r["n_intervals"],
        )
        for r in rows
    ]
    with conn.cursor() as cur:
        execute_values(cur, STORE_SQL, values)
    conn.commit()
    return len(values)


def main() -> None:
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        rows = score(conn)
        n_stored = store(conn, rows)
    finally:
        conn.close()

    # No rows is not an error, but it is not nothing either: it usually means
    # the prediction service did not run before the day it was predicting.
    if not rows:
        print("no scored days -- no prediction was made before its target date")
        return

    print(f"{'target_date':12} {'model':>7} {'predicted':>10} "
          f"{'actual':>10} {'abs_error':>10}")
    for r in rows:
        print(f"{str(r['target_date']):12} {r['model_revision']:>7} "
              f"{r['y_hat']:>10.4f} {r['actual']:>10.4f} {r['abs_error']:>10.4f}")

    mae = sum(float(r["abs_error"]) for r in rows) / len(rows)
    worst = max(rows, key=lambda r: r["abs_error"])
    print(f"\n{len(rows)} scored days   MAE {mae:.4f} SEK/kWh")
    print(f"worst day {worst['target_date']}  "
          f"off by {float(worst['abs_error']):.4f}")
    print(f"wrote {n_stored} rows to monitor__daily")


if __name__ == "__main__":
    main()
