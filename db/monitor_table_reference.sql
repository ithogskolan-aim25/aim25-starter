-- Teacher reference: one row per day the model has been graded on.
--
-- Why store this at all, when monitor_reference.py can recompute it from
-- pred__log and stg__elpris on demand? Because both of those inputs move.
-- Prices get re-ingested and stg__elpris picks the newest ingestion, so a
-- score computed today can quietly differ from the same score computed next
-- week. A monitoring series whose history rewrites itself cannot tell you
-- whether the model got worse. This table freezes the grade.

CREATE TABLE IF NOT EXISTS monitor__daily (
    price_area        TEXT NOT NULL,
    target_date       DATE NOT NULL,

    -- Which prediction was graded. Keeping the id and the timestamp means
    -- you can always walk back to the exact row in pred__log, with the
    -- features it was built from -- component D's traceability question.
    prediction_id     BIGINT NOT NULL,
    predicted_at      TIMESTAMPTZ NOT NULL,
    model_revision    TEXT NOT NULL,

    y_hat             DOUBLE PRECISION NOT NULL,
    actual            DOUBLE PRECISION NOT NULL,

    -- Signed error as well as absolute. MAE alone cannot see bias: a model
    -- that is always 0.6 too low and one that is wildly wrong in both
    -- directions score the same. Averaging the signed error separates them,
    -- and a model drifting steadily in one direction is the failure mode a
    -- daily job is most likely to catch first.
    error             DOUBLE PRECISION
                      GENERATED ALWAYS AS (y_hat - actual) STORED,
    abs_error         DOUBLE PRECISION
                      GENERATED ALWAYS AS (abs(y_hat - actual)) STORED,

    -- The same baseline train.py insists on, carried into production:
    -- "tomorrow will be like today", i.e. the previous day's actual mean.
    -- Without it, an MAE is a number with nothing to be better than.
    naive_y_hat       DOUBLE PRECISION,
    naive_abs_error   DOUBLE PRECISION
                      GENERATED ALWAYS AS (abs(naive_y_hat - actual)) STORED,

    -- How many price intervals the actual was averaged over: 24, or 96 since
    -- the resolution change, or fewer on a day that arrived incomplete. A
    -- sudden bad score on a day with 7 intervals is a data incident, not a
    -- model incident, and this column is how you tell them apart.
    n_intervals       INTEGER,

    scored_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One grade per day per area. Re-running the job re-scores in place.
    PRIMARY KEY (price_area, target_date)
);

-- No percentage error on purpose. Electricity prices go to zero and below,
-- so MAPE divides by something near nothing and reports thousands of
-- percent on a day nobody cares about. If you want a relative measure,
-- scale by a rolling mean price, not by the day's own value.

-- ---------------------------------------------------------------------------
-- Trend, derived rather than stored, so it can never disagree with the table.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW monitor__rolling AS
SELECT
    price_area,
    target_date,
    model_revision,
    abs_error,
    avg(abs_error)       OVER w AS mae_7d,
    avg(naive_abs_error) OVER w AS naive_mae_7d,
    avg(error)           OVER w AS bias_7d,
    count(*)             OVER w AS days_in_window
FROM monitor__daily
WINDOW w AS (
    PARTITION BY price_area
    ORDER BY target_date
    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
);
