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
