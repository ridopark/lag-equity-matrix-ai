"""Daily market data from Alpaca (D-16).

The pipeline runs on a daily cadence (D-15), so this fetches end-of-day bars
rather than streaming ticks. `adjustment="all"` is not optional: unadjusted
closes make a split look like a shock.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

BATCH = 200


class MarketFeed:
    def __init__(self, api_key: str | None = None, secret_key: str | None = None):
        self._client = StockHistoricalDataClient(
            api_key or os.environ["ALPACA_API_KEY"],
            secret_key or os.environ["ALPACA_SECRET_KEY"],
        )

    def daily_closes(self, symbols: list[str], start: date, end: date) -> pd.DataFrame:
        """Adjusted closes as a date x symbol frame. Missing symbols are dropped."""
        frames = []
        for i in range(0, len(symbols), BATCH):
            req = StockBarsRequest(
                symbol_or_symbols=symbols[i : i + BATCH],
                timeframe=TimeFrame.Day,
                start=start,
                end=end + timedelta(days=1),
                adjustment="all",
            )
            df = self._client.get_stock_bars(req).df
            if not df.empty:
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        bars = pd.concat(frames).reset_index()
        bars["date"] = pd.to_datetime(bars.timestamp, utc=True).dt.normalize()
        return bars.pivot_table(index="date", columns="symbol", values="close")
