"""Simulated market data so the whole pipeline can be tried without internet.

Used by `python run.py --demo` and by the tests. The numbers are NOT real.
"""
from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

from .fundamentals import evaluate


def synthetic_prices(constituents: list[dict], end: str | None = None, days: int = 520, seed: int = 27):
    rng = np.random.default_rng(seed)
    end_ts = pd.Timestamp(end) if end else pd.Timestamp.today().normalize()
    dates = pd.bdate_range(end=end_ts, periods=days)

    # market path: rally, correction, rally, two-leg correction, recovery (array repeats to fill)
    segments = [(150, 0.0009), (80, -0.0011), (100, 0.0010), (60, -0.0012), (65, -0.0013), (30, 0.0020)]
    drift = np.concatenate([np.full(n, d) for n, d in segments])
    drift = np.resize(drift, days)
    mkt = drift + rng.normal(0, 0.0075, days)

    sectors = sorted({c["sector"] for c in constituents})
    sec_bias = {s: rng.normal(0, 0.0005) for s in sectors}
    # make a few sectors clear leaders/laggards in the recent recovery
    recent = np.zeros(days)
    recent[-60:] = 1
    leaders = set(sectors[i] for i in rng.choice(len(sectors), 4, replace=False))
    laggards = set(sectors[i] for i in rng.choice(len(sectors), 3, replace=False)) - leaders
    sec_ret = {}
    for s in sectors:
        extra = 0.0011 if s in leaders else (-0.0012 if s in laggards else 0.0)
        sec_ret[s] = sec_bias[s] + extra * recent + rng.normal(0, 0.005, days)

    close = {}
    for c in constituents:
        beta = rng.uniform(0.7, 1.3)
        idio_drift = rng.normal(0, 0.0004)
        r = beta * mkt + sec_ret[c["sector"]] + idio_drift + rng.normal(0, 0.011, days)
        start = rng.uniform(200, 6000)
        close[c["symbol"]] = start * np.exp(np.cumsum(r))
    close = pd.DataFrame(close, index=dates)
    nifty = pd.Series(22000 * np.exp(np.cumsum(mkt + 0.0001)), index=dates, name="NIFTY")
    return close.round(2), nifty.round(2)


def synthetic_fundamentals(symbol: str, sector: str, cfg: dict) -> dict:
    rng = np.random.default_rng(zlib.crc32(symbol.encode()))
    g = rng.normal(0.14, 0.07)
    rev0 = rng.uniform(8e10, 9e11)
    revenue = [rev0 / (1 + g) ** i for i in range(4)]
    margin = rng.uniform(0.06, 0.22)
    ni_g = g + rng.normal(0, 0.04)
    net_income = [revenue[0] * margin / (1 + ni_g) ** i for i in range(4)]
    ta = [revenue[0] * rng.uniform(0.9, 2.2) / (1 + g * 0.8) ** i for i in range(4)]
    is_fin = sector == "Financial Services"
    raw = {
        "revenue": revenue,
        "net_income": net_income,
        "gross_profit": [] if is_fin else [r * rng.uniform(0.3, 0.5) for r in revenue],
        "total_assets": ta,
        "current_assets": [] if is_fin else [t * rng.uniform(0.3, 0.5) for t in ta],
        "current_liabilities": [] if is_fin else [t * rng.uniform(0.2, 0.35) for t in ta],
        "long_term_debt": [t * rng.uniform(0.0, 0.25) for t in ta],
        "total_debt": [t * rng.uniform(0.0, 0.3) for t in ta],
        "equity": [t * rng.uniform(0.4, 0.7) for t in ta],
        "shares": [1e9, 1e9, 1e9 * rng.choice([1.0, 0.98]), 1e9],
        "ocf": [n * rng.uniform(0.8, 1.4) for n in net_income],
        "market_cap": net_income[0] * rng.uniform(18, 60),
        "de_info": None,
        "peg_info": rng.uniform(0.6, 2.6),
        "pe": None, "price": None, "shares_out": None,
    }
    pledge = float(rng.choice([0.0, 0.0, 0.0, 0.0, 2.5]))
    return evaluate(raw, sector, cfg, pledge, "simulated")
