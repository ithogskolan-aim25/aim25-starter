-- Minimal prediction log for either forecast track.
-- Run once in the same PostgreSQL database as feat__daily.
-- This file creates only the log; api/main.py does not yet insert rows.
--
-- One row per successful prediction, including repeated requests for the
-- same area and date. Do not make (price_area, target_date) unique.

CREATE TABLE IF NOT EXISTS pred__log (
    prediction_id     BIGSERIAL PRIMARY KEY,
    predicted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    price_area        TEXT NOT NULL,
    target_date       DATE NOT NULL,
    y_hat             DOUBLE PRECISION NOT NULL, -- SEK/kWh, as returned by /predict
    model_repo        TEXT NOT NULL,             -- HF_MODEL_REPO
    model_revision    TEXT NOT NULL,             -- HF_REVISION
    feature_built_at  TIMESTAMPTZ,               -- feat__daily.built_at; NULL for live features
    features          JSONB NOT NULL,            -- exact model inputs incl. JSON nulls
    code_revision     TEXT,                      -- optional deployment/git commit
    CONSTRAINT pred__log_features_object CHECK (jsonb_typeof(features) = 'object')
);

CREATE INDEX IF NOT EXISTS pred__log_area_date_idx
    ON pred__log (price_area, target_date);

-- In api/main.py, insert only AFTER model.predict succeeds. Use the same
-- feature row and ordered feature names as the prediction; do not re-query
-- feat__daily to create the log. In a psycopg2 cursor, pass values with %s
-- placeholders and wrap the snapshot with psycopg2.extras.Json, converting
-- pandas/numpy values and missing values to JSON-native numbers/null first:
--
-- INSERT INTO pred__log (
--     price_area, target_date, y_hat, model_repo, model_revision,
--     feature_built_at, features, code_revision
-- ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
-- RETURNING prediction_id;
--
-- Values for track 2: PRICE_AREA, row["target_date"], prediction,
-- HF_MODEL_REPO, HF_REVISION, row["built_at"], Json(feature_snapshot),
-- optional git commit. For track 1, feature_built_at is NULL because the
-- inputs are assembled in the API. The snapshot must still contain the
-- actual price, calendar and forecast values supplied to the model.
-- Commit the transaction before returning success. Decide explicitly whether
-- a failed log insert makes /predict fail; otherwise a served prediction can
-- disappear from the audit trail.
--
-- The outcome feat__daily.y is not copied here: it may be NULL at prediction
-- time and may be filled in later. To compare each logged prediction with the
-- currently known outcome, query:
--
-- SELECT p.prediction_id, p.predicted_at, p.price_area, p.target_date,
--        p.y_hat, f.y AS y_true
-- FROM pred__log AS p
-- LEFT JOIN feat__daily AS f
--   ON f.price_area = p.price_area AND f.target_date = p.target_date;
--
-- For complete "which code?" traceability, populate code_revision at
-- deployment time; the starter API has no code-version environment variable.
