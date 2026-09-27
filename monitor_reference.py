"""
Score yesterday's predictions against what the price actually turned out to be.

    uv run python monitor_reference.py

Add it to the daily workflow after the feature pipeline, and every morning
it tells you how the model did on the days that have now settled.

The SQL below answers one question: for each finished day, what did we
predict BEFORE that day began, and what was the price really? Two details
in it carry the whole idea.

`predicted_at < target_date midnight` is the no-peeking rule. pred__log
contains backfilled and re-run predictions made after the fact, and those
are not predictions -- scoring them would flatter the model with knowledge
it did not have. This is the same information boundary as in train.py,
enforced at scoring time instead of training time.

`DISTINCT ON (target_date) ... ORDER BY predicted_at DESC` then keeps one
prediction per day: the last one made while it was still a forecast. Run
the service five times in an evening and the day is still scored once.

Edge cases are deliberately not handled -- a day with partial prices is
averaged over whatever intervals arrived, and a day with no qualifying
prediction simply does not appear. Good enough to watch a model drift;
not good enough to bill anyone on.
"""

from __future__ import annotations

import os

import psycopg2
from dotenv import load_dotenv

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
        p.model_revision, p.y_hat, d.actual,
        abs(p.y_hat - d.actual) AS abs_error
    FROM chosen_predictions p
    JOIN daily_prices d
      ON d.price_date = p.target_date
)
SELECT * FROM scored
ORDER BY target_date
"""


def score(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(SCORE_SQL)
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def main() -> None:
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        rows = score(conn)
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


if __name__ == "__main__":
    main()
