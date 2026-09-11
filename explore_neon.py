"""
Throwaway script -- example queries for poking at your own Neon database.
Not part of the pipeline, not something to import from anywhere. Copy
individual queries out of here into your own scratch checks, or run the
whole thing and read the output.

    uv run python explore_neon.py

Requires DATABASE_URL in your environment (.env, same as everywhere else).
Set PRICE_AREA too, or edit AREA below.

Everything here works against the tables you have now: raw__elpris,
raw__weather, and the two stg__ views. The feature-table section at the end
is skipped automatically until you build feat__daily -- so this script keeps
working as your database grows, rather than needing edits next week.
"""

import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()
AREA = os.getenv("PRICE_AREA", "SE3")


def run(cur, label: str, sql: str, params=()):
    """Run one query, print a label, then the rows. Every example below
    follows this same pattern -- the interesting part is the SQL."""
    print(f"\n--- {label} " + "-" * max(0, 62 - len(label)))
    cur.execute(sql, params)
    if cur.description:
        print("  " + " | ".join(c.name for c in cur.description))
    rows = cur.fetchall()
    if not rows:
        print("  (no rows)")
    for row in rows:
        print("  " + " | ".join(str(v) for v in row))


def table_exists(cur, name: str) -> bool:
    """to_regclass returns NULL for a name that isn't a table or view,
    instead of raising. Handy for 'run this bit only if it exists'."""
    cur.execute("SELECT to_regclass(%s)", (name,))
    return cur.fetchone()[0] is not None


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()

    # ---------------------------------------------------------------- raw
    # 1. How much is in each table? The first thing to check when
    # something downstream looks wrong.
    run(cur, "row counts per table",
        """
        SELECT 'raw__elpris'  AS table_name, count(*) FROM raw__elpris
        UNION ALL SELECT 'raw__weather',  count(*) FROM raw__weather
        UNION ALL SELECT 'stg__elpris',   count(*) FROM stg__elpris
        UNION ALL SELECT 'stg__weather',  count(*) FROM stg__weather
        """)

    # 2. Which parameters did your ingestion actually write? If wind is
    # missing here, this is the 98230 bug -- the fetch "worked" and the
    # parameter is simply absent. Also confirms whether your codes are
    # SMHI's ids ('1', '4') or names, which downstream code has to match.
    run(cur, "distinct weather parameters present in raw__weather",
        "SELECT parameter, count(*) FROM raw__weather GROUP BY parameter ORDER BY parameter")

    # 3. What period do you actually have? Prices and weather answer this
    # separately on purpose -- they can disagree, and that gap is a bug.
    run(cur, "coverage: prices vs weather",
        """
        SELECT 'prices'  AS source,
               min(price_date)::text AS earliest,
               max(price_date)::text AS latest
        FROM raw__elpris WHERE price_area = %s
        UNION ALL
        SELECT 'weather',
               min(observed_at)::text,
               max(observed_at)::text
        FROM raw__weather
        """,
        (AREA,))

    # ---------------------------------------------------------------- stg
    # 4. Actual rows, so you can see the real shape rather than guessing
    # from the schema. stg__elpris unpacks the JSON into typed columns.
    run(cur, "5 sample rows from stg__elpris",
        "SELECT * FROM stg__elpris WHERE price_area = %s ORDER BY starts_at DESC LIMIT 5",
        (AREA,))

    run(cur, "5 sample rows from stg__weather",
        "SELECT * FROM stg__weather ORDER BY observed_at DESC LIMIT 5")

    # 5. The acceptance test from Thursday, as one query. Run your
    # ingestion twice, then run this: raw MUST grow, stg MUST NOT.
    run(cur, "append-only raw vs deduplicated stg",
        """
        SELECT (SELECT count(*) FROM raw__elpris)  AS raw_prices,
               (SELECT count(*) FROM stg__elpris)  AS stg_prices,
               (SELECT count(*) FROM raw__weather) AS raw_weather,
               (SELECT count(*) FROM stg__weather) AS stg_weather
        """)

    # 6. Gaps in the weather series. observed_at is a TIMESTAMP, so
    # subtracting two of them gives an INTERVAL, not a number -- extract
    # epoch seconds and divide to get hours. Only gaps over 1 hour print,
    # so an unbroken hourly series produces no rows at all.
    run(cur, "gaps over 1 hour in stg__weather (temperature)",
        """
        WITH ordered AS (
            SELECT observed_at,
                   lead(observed_at) OVER (ORDER BY observed_at) AS next_at
            FROM stg__weather
            WHERE parameter = '1'
        )
        SELECT observed_at::text AS last_reading_before,
               next_at::text     AS first_reading_after,
               -- minus 1: two readings an hour apart have nothing missing
               -- between them, so subtract the step to count MISSING hours
               -- rather than elapsed hours. A 09:00 -> 15:00 jump is 6 hours
               -- elapsed but 5 absent readings, and 5 is the number that
               -- matches what check_weather_coverage.py reports.
               (extract(epoch FROM next_at - observed_at) / 3600)::int - 1 AS hours_missing
        FROM ordered
        WHERE next_at - observed_at > interval '1 hour'
        ORDER BY next_at - observed_at DESC
        LIMIT 10
        """)

    # 7. SMHI's own quality flags. G = checked and approved, Y = coarser
    # control. train.py currently keeps both -- this tells you how much of
    # your data that decision actually covers.
    run(cur, "weather quality flags",
        """
        SELECT parameter, quality, count(*)
        FROM stg__weather
        GROUP BY parameter, quality
        ORDER BY parameter, quality
        """)

    # 8. Empty values inside rows that DO exist -- the second shape of
    # missing data, and the one a row count will never show you.
    run(cur, "rows present but value is NULL",
        """
        SELECT parameter,
               count(*)                              AS rows_total,
               count(*) FILTER (WHERE value IS NULL) AS value_null
        FROM stg__weather
        GROUP BY parameter
        ORDER BY parameter
        """)

    # ------------------------------------------------------- feat (later)
    # Skipped until you build it -- see feature_pipeline_notes.md.
    if table_exists(cur, "feat__daily"):
        run(cur, "feature table coverage and freshness",
            """
            SELECT min(date)::text  AS earliest,
                   max(date)::text  AS latest,
                   max(built_at)::text AS last_refreshed,
                   count(*)         AS rows
            FROM feat__daily WHERE price_area = %s
            """,
            (AREA,))
    else:
        print("\n--- feat__daily " + "-" * 47)
        print("  not built yet -- skipping. See feature_pipeline_notes.md.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
