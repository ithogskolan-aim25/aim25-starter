"""
Check that your fetched weather data is actually usable.

    uv run python data/check_weather_coverage.py

Run it straight after fetch_historik.py, before you load anything.

Missing data has two shapes, and a check that knows one is blind to the other:

    the row is there and the value is empty
    the row is not there at all

This counts both the same way. It lays every hour between the first and last
observation on a grid and asks, for each parameter, which hours have no usable
value — whether that is because the row is absent or because the cell is empty.

Scattered gaps in old data are normal and are only reported. What fails the
check is a parameter that is empty, one that stopped early, or a hole in the
recent data — because that is the period you are going to predict on.
"""

import sys
from pathlib import Path

import pandas as pd

CSV = Path(__file__).parent / "historik_vader.csv.gz"
PARAMS = ["Lufttemperatur", "Vindhastighet"]
REPORT_GAP_HOURS = 24     # gaps at least this long are worth showing
RECENT_DAYS = 30          # a hole in this window is what actually breaks you
RECENT_MIN_HOURS = 6      # ...but a stray missing hour is normal everywhere


def gaps(missing_hours):
    """Group a sorted index of missing hours into consecutive runs."""
    if len(missing_hours) == 0:
        return []
    runs, start, prev = [], missing_hours[0], missing_hours[0]
    for h in missing_hours[1:]:
        if (h - prev) > pd.Timedelta(hours=1):
            runs.append((start, prev))
            start = h
        prev = h
    runs.append((start, prev))
    return runs


def main():
    df = pd.read_csv(CSV, sep=";")
    df = df.rename(columns={"Tid (UTC)": "tid", "Datum": "datum"})
    df["observed_at"] = pd.to_datetime(df.datum + " " + df.tid)

    bad = warned = False
    for station, sdf in df.groupby("Stationsnummer"):
        print(f"\nstation {station}  {sdf.Stationsnamn.iloc[0]}")
        print(f"  rows {len(sdf):,}   {sdf.datum.min()} -> {sdf.datum.max()}")

        # Every hour that should exist. The column is UTC, which has no DST,
        # so a plain hourly range is the right grid.
        grid = pd.date_range(sdf.observed_at.min(), sdf.observed_at.max(), freq="h")

        for param in PARAMS:
            series = (sdf.dropna(subset=[param])
                         .drop_duplicates("observed_at")
                         .set_index("observed_at")[param]
                         .reindex(grid))
            missing = series[series.isna()].index
            pct = 100 * len(missing) / len(grid)
            runs = gaps(missing)
            longest = max((e - s + pd.Timedelta(hours=1) for s, e in runs),
                          default=pd.Timedelta(0))

            print(f"  {param:<16} missing {pct:5.1f}% of {len(grid):,} hours", end="")

            big = [(a, b) for a, b in runs
                   if (b - a) >= pd.Timedelta(hours=REPORT_GAP_HOURS - 1)]
            recent_cutoff = grid[-1] - pd.Timedelta(days=RECENT_DAYS)
            recent = [(a, b) for a, b in runs if b >= recent_cutoff
                      and (b - a) >= pd.Timedelta(hours=RECENT_MIN_HOURS - 1)]

            if len(missing) == len(grid):
                print("   <-- NOTHING AT ALL")
                bad = True
            elif recent:
                a, b = max(recent, key=lambda r: r[1] - r[0])
                d = b - a + pd.Timedelta(hours=1)
                print(f"   <-- {int(d.total_seconds() // 3600)}h GAP IN THE LAST "
                      f"{RECENT_DAYS} DAYS ({a:%Y-%m-%d %H:%M})")
                warned = True
            elif big:
                a, b = max(big, key=lambda r: r[1] - r[0])
                d = b - a + pd.Timedelta(hours=1)
                print(f"   longest gap {d.days}d {d.seconds // 3600}h "
                      f"({a:%Y-%m-%d}), {len(big)} gaps over a day")
            elif runs:
                print(f"   {len(runs)} short gaps, none over a day")
            else:
                print("   no gaps")

        last = {p: sdf.loc[sdf[p].notna(), "observed_at"].max() for p in PARAMS}
        spread = max(last.values()) - min(last.values())
        if spread > pd.Timedelta(hours=48):
            print(f"  !! parameters stop {spread.days} days apart: "
                  + ", ".join(f"{p} {t:%Y-%m-%d}" for p, t in last.items()))
            bad = True

    print()
    if bad:
        print("NOT OK. This station cannot carry your project: a parameter is")
        print("missing or stopped early. Pick another one and re-fetch.")
        print('  uv run python data/fetch_historik.py --find-station "Stockholm"')
        return 1
    if warned:
        print(f"USABLE, but read the marked lines: something is missing inside the")
        print(f"last {RECENT_DAYS} days. You cannot fix that by switching station —")
        print("decide how you handle it when you build features, and write it down.")
        return 0
    print("OK: both parameters present, both current, no recent hole.")
    print("Older gaps above are normal. Decide how you fill them, and say why.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
