-- Teacher reference for the first feature-pipeline version.
-- One row describes the information available when predicting target_date.

CREATE TABLE IF NOT EXISTS feat__daily (
    price_area             TEXT NOT NULL,
    target_date            DATE NOT NULL,
    tomorrow_temp_mean     DOUBLE PRECISION,
    tomorrow_temp_min      DOUBLE PRECISION,
    tomorrow_temp_max      DOUBLE PRECISION,
    tomorrow_wind_mean     DOUBLE PRECISION,
    price_today            DOUBLE PRECISION,
    price_yesterday        DOUBLE PRECISION,
    price_7d_ago           DOUBLE PRECISION,
    price_7d_mean          DOUBLE PRECISION,
    peak_today             DOUBLE PRECISION,
    dayofweek              SMALLINT,
    month                  SMALLINT,
    is_weekend             BOOLEAN,
    y                      DOUBLE PRECISION,
    built_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (price_area, target_date)
);
