"""
Throwaway target for testing GitHub Actions mechanics — not real ingestion.

Delete this once your real daily_ingest.py exists. It exercises exactly the
things a real scheduled job needs, with no database and no network:

    - reads a CLI arg (--days), same shape as the real script
    - reads an env var that should come from a *secret*
    - reads an env var that should come from a *variable*
    - "does work" (just sleeps) so you can watch a run take real time
    - exits non-zero if nothing was configured, so you can watch a run go red

Test plan:
    1. Commit this + a workflow that runs it with workflow_dispatch only.
       Press Run. Watch it go green.
    2. Add DATABASE_URL as a repo *secret* (any string, e.g. "placeholder")
       and PRICE_AREA as a repo *variable* (e.g. "SE3"). Re-run. Read the
       log: the secret should print as ***, the variable should print plainly.
    3. Rename the secret in the workflow YAML to something wrong. Push.
       Run. Watch it fail, and read what the failure actually looks like.
    4. Add the schedule: block and leave it — you don't need to wait for it
       to fire to know the YAML is valid; workflow_dispatch already proved that.
"""

import argparse
import os
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    price_area = os.environ.get("PRICE_AREA")

    print(f"would ingest the last {args.days} days for area={price_area!r}")
    print(f"DATABASE_URL is {'set' if database_url else 'MISSING'}")

    if not database_url or not price_area:
        print("missing config — a real job would have nothing to write to", file=sys.stderr)
        return 1

    print("pretending to fetch and write rows...")
    time.sleep(3)
    rows_written = 42  # stand-in — the real script returns an actual count
    print(f"wrote {rows_written} rows")

    if rows_written == 0:
        print("zero rows written — failing on purpose, see Lecture 7.2", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
