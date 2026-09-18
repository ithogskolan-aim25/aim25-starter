CREATE OR REPLACE VIEW stg__forecast AS
SELECT DISTINCT ON (station, observed_at)
    station,
    observed_at,
    (data -> 'data' ->> 'air_temperature')::numeric AS temp_c,
    (data -> 'data' ->> 'wind_speed')::numeric AS wind_ms,
    ingestion_timestamp
FROM raw__weather
WHERE parameter = 'forecast'
ORDER BY station, observed_at, ingestion_timestamp DESC;
