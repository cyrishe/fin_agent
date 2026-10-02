"""Deterministic synthetic market, explicitly not investment evidence."""
import numpy as np
import pandas as pd


def synthetic_market(seed=42, companies=8, sessions=260):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=sessions)
    rows, values = [], []
    market = rng.normal(.0003, .008, sessions)
    for i in range(companies):
        symbol = f"{i+1:06d}.SZ"
        price = 20 + i * 3
        for j, day in enumerate(dates):
            previous = price
            op = previous * np.exp(rng.normal(0, .002))
            price = op * np.exp(market[j] + rng.normal(0, .012))
            high = max(op, price) * 1.005
            low = min(op, price) * .995
            volume = int(rng.uniform(1e6, 5e6))
            rows.append(dict(symbol=symbol, date=day, open=op, high=high, low=low, close=price,
                             preclose=previous, adjopen=op, adjhigh=high, adjlow=low, adjclose=price,
                             volume=volume, amount=volume*price, turn_ratio=2., industry=f"industry_{i%3}"))
            values.append(dict(symbol=symbol, available_at=day + pd.Timedelta(days=1),
                               total_mv=price*(i+1)*1e8, pe_ttm=10+i, news_count_7d=float(j%9)))
    return pd.DataFrame(rows), [pd.DataFrame(values)]
