from datetime import date

import pandas as pd

from model.train import daily_price_table


def test_daily_price_table_counts_intervals():
    prices = pd.DataFrame(
        {
            "date_local": [date(2026, 1, 1)] * 2,
            "SEK_per_kWh": [0.40, 0.60],
        }
    )

    daily = daily_price_table(prices)

    assert daily.loc["2026-01-01", "n_intervals"] == 2
