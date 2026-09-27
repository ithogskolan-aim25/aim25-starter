-- Example monitor table for daily scoring.
CREATE TABLE IF NOT EXISTS monitor__daily (
    price_area        TEXT NOT NULL,
    target_date       DATE NOT NULL,
    -- Which prediction was graded. 
    prediction_id     BIGINT NOT NULL,
    predicted_at      TIMESTAMPTZ NOT NULL,
    model_revision    TEXT NOT NULL,
    y_hat             DOUBLE PRECISION NOT NULL,
    actual            DOUBLE PRECISION NOT NULL,
    -- Signed and absolute error.
    error             DOUBLE PRECISION
                      GENERATED ALWAYS AS (y_hat - actual) STORED,
    abs_error         DOUBLE PRECISION
                      GENERATED ALWAYS AS (abs(y_hat - actual)) STORED,
    -- Naive prediction: the previous day's actual mean.
    naive_y_hat       DOUBLE PRECISION,
    naive_abs_error   DOUBLE PRECISION
                      GENERATED ALWAYS AS (abs(naive_y_hat - actual)) STORED,
    -- How many price intervals the actual was averaged over
    n_intervals       INTEGER,
    scored_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (price_area, target_date)
);

-- ---------------------------------------------------------------------------
-- Rolling trend analysis.
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
