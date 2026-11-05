"""Prices, market breadth, swing-timing model, sector breadth and sector leaders."""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

from .universe import short_sector, yahoo_symbol

log = logging.getLogger("vestora")

NIFTY_TICKER = "^NSEI"

# ---------------------------------------------------------------------------
# Market modes (the swing-timing verdict)
# ---------------------------------------------------------------------------
REGIMES = {
    "THRUST": {
        "label": "BREADTH THRUST",
        "tone": "best",
        "advice": "Rare, broad-based surge in breadth — historically one of the strongest "
                  "swing-buy windows. Buy leaders from strong sectors on small dips.",
    },
    "BEST": {
        "label": "BEST TIME FOR SWINGS",
        "tone": "best",
        "advice": "Broad participation and holding up. Take swing entries in leaders "
                  "from strong sectors with normal position size.",
    },
    "GOOD": {
        "label": "GOOD — BE SELECTIVE",
        "tone": "good",
        "advice": "Breadth is improving. Trade only the strongest stocks; keep size moderate.",
    },
    "OVERHEATED": {
        "label": "OVERHEATED — DON'T CHASE",
        "tone": "warn",
        "advice": "Most stocks are already stretched above their 50 DMA. Book partial "
                  "profits, trail stops and wait for a pullback before new entries.",
    },
    "FADING": {
        "label": "FADING — TIGHTEN STOPS",
        "tone": "warn",
        "advice": "Breadth is rolling over from healthy levels. Protect profits and take "
                  "fewer new entries.",
    },
    "NEUTRAL": {
        "label": "NEUTRAL — WAIT",
        "tone": "neutral",
        "advice": "No clear edge from breadth. Keep positions small or wait for a better setup.",
    },
    "EARLY_REVERSAL": {
        "label": "EARLY REVERSAL",
        "tone": "good",
        "advice": "Breadth is turning up from oversold. Build a watchlist; small starter "
                  "positions only until breadth confirms above 50%.",
    },
    "OVERSOLD": {
        "label": "OVERSOLD — WATCH",
        "tone": "bad",
        "advice": "Breadth is washed out. Don't catch falling knives — wait for breadth to turn up.",
    },
    "AVOID": {
        "label": "AVOID NEW SWINGS",
        "tone": "bad",
        "advice": "Most Nifty 50 stocks are below their 50 DMA and breadth is falling. "
                  "Stay light; cash is a position.",
    },
}


# ---------------------------------------------------------------------------
# Data download
# ---------------------------------------------------------------------------
def fetch_prices(symbols: list[str], period: str = "2y", retries: int = 3) -> tuple[pd.DataFrame, pd.Series]:
    """Daily adjusted closes for the given NSE symbols plus the Nifty 50 index.

    Returns (close DataFrame indexed by date with NSE symbols as columns, nifty Series).
    """
    import yfinance as yf  # imported here so demo/tests run without it

    tickers = [yahoo_symbol(s) for s in symbols] + [NIFTY_TICKER]
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            raw = yf.download(
                tickers, period=period, interval="1d", auto_adjust=True,
                progress=False, threads=True, group_by="column",
            )
            if raw is None or raw.empty:
                raise RuntimeError("empty download")
            close = raw["Close"].copy()
            close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
            missing = [t for t in tickers if t not in close.columns or close[t].dropna().empty]
            if missing:
                log.warning("Retrying %d tickers individually: %s", len(missing), missing)
                for t in missing:
                    try:
                        one = yf.download(t, period=period, interval="1d", auto_adjust=True, progress=False)
                        if one is not None and not one.empty:
                            s = one["Close"]
                            if isinstance(s, pd.DataFrame):
                                s = s.iloc[:, 0]
                            s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                            close[t] = s
                    except Exception as exc:  # keep going with what we have
                        log.warning("  %s failed: %s", t, exc)
            nifty = close.pop(NIFTY_TICKER) if NIFTY_TICKER in close.columns else None
            if nifty is None or nifty.dropna().empty:
                raise RuntimeError("Nifty 50 index data missing")
            close.columns = [c[:-3] if c.endswith(".NS") else c for c in close.columns]
            close = close.sort_index()
            got = [s for s in symbols if s in close.columns and not close[s].dropna().empty]
            log.info("Prices: %d/%d stocks, %d sessions, last %s",
                     len(got), len(symbols), len(close), close.index[-1].date())
            if len(got) < 40:
                raise RuntimeError(f"only {len(got)} stocks downloaded")
            return close[got], nifty.sort_index()
        except Exception as exc:
            last_exc = exc
            wait = 20 * attempt
            log.warning("Price download attempt %d failed: %s (waiting %ss)", attempt, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"Could not download prices: {last_exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _r(x, nd=2):
    """Round for JSON; NaN -> None."""
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    return round(float(x), nd)


def _pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True).fillna(0.5)


def classify_regime(b50: float, d5: float, nifty_above: bool, thrust_recent: bool, cfg: dict) -> str:
    c = cfg["breadth"]
    if thrust_recent and b50 >= c["best_zone_min"]:
        return "THRUST"
    if b50 >= c["overheated_min"]:
        return "OVERHEATED"
    if b50 >= c["best_zone_min"] and d5 >= -5 and nifty_above:
        return "BEST"
    if b50 >= c["good_zone_min"] and d5 > 0:
        return "GOOD"
    if b50 >= c["good_zone_min"] and d5 <= -5:
        return "FADING"
    if b50 <= c["oversold_max"] and d5 >= c["reversal_jump"]:
        return "EARLY_REVERSAL"
    if b50 <= c["oversold_max"]:
        return "OVERSOLD"
    if b50 < c["weak_zone_max"] and d5 <= 0:
        return "AVOID"
    return "NEUTRAL"


def swing_score(b50: float, d5: float, b200: float, nifty_above: bool, nifty_sma_rising: bool, cfg: dict) -> float:
    """0-100 composite: breadth level 35%, breadth momentum 25%, long-term breadth 20%, index trend 20%."""
    lvl = b50
    mom = float(np.clip(50 + d5 * 2.5, 0, 100))
    trend = b200 if b200 == b200 else 50.0
    idx = 50.0 * bool(nifty_above) + 50.0 * bool(nifty_sma_rising)
    score = 0.35 * lvl + 0.25 * mom + 0.20 * trend + 0.20 * idx
    if b50 >= cfg["breadth"]["overheated_min"]:
        score -= 15  # stretched market: good for holding, poor for fresh swing entries
    return float(np.clip(score, 0, 100))


def build_watchlist(stocks: list[dict], sectors: list[dict], previous: list[str] | None):
    """Vestora watchlist = technical leaders inside sectors with good breadth.

    A stock already on the list stays while it holds its 50 DMA (small buffer) and its
    sector keeps reasonable breadth, so alerts don't flip-flop on one-day noise.
    """
    by_sym = {x["symbol"]: x for x in stocks}
    sec = {x["name"]: x for x in sectors}
    entries = [x for x in stocks if x["leader"] and sec[x["sector"]]["good"]]
    names = {x["symbol"] for x in entries}
    for s in previous or []:
        st = by_sym.get(s)
        if st and s not in names and st["hold_ok"] and sec[st["sector"]]["hold"]:
            entries.append(st)
            names.add(s)
    entries.sort(key=lambda x: -(x["score"] or 0))
    watchlist = [x["symbol"] for x in entries]
    for x in stocks:
        x["in_watchlist"] = x["symbol"] in names

    by_sector = []
    for sct in sectors:
        picks = [m for m in sct["members"] if by_sym[m]["in_watchlist"]]
        others = [m for m in sct["members"] if by_sym[m]["leader"] and m not in names]
        by_sector.append({
            "sector": sct["name"], "short": sct["short"], "good": sct["good"],
            "status": sct["status"], "b50": sct["b50"], "above": sct["above"], "n": sct["n"],
            "picks": picks, "other_leaders": others,
        })
    return watchlist, by_sector


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------
def analyse(close: pd.DataFrame, nifty: pd.Series, constituents: list[dict], cfg: dict) -> dict:
    bc, lc, sc = cfg["breadth"], cfg["leaders"], cfg["sectors"]
    meta = {c["symbol"]: c for c in constituents}
    syms = [s for s in meta if s in close.columns]
    px = close[syms].sort_index().ffill(limit=3)
    nifty = nifty.sort_index().reindex(px.index).ffill()

    ns, nl = int(bc["dma_short"]), int(bc["dma_long"])
    sma_s = px.rolling(ns, min_periods=ns).mean()
    sma_l = px.rolling(nl, min_periods=nl).mean()

    valid_s = sma_s.notna() & px.notna()
    above_s = (px > sma_s) & valid_s
    valid_l = sma_l.notna() & px.notna()
    above_l = (px > sma_l) & valid_l

    cnt_s = valid_s.sum(axis=1)
    cnt_l = valid_l.sum(axis=1)
    b50 = (above_s.sum(axis=1) / cnt_s.replace(0, np.nan) * 100)
    b200 = (above_l.sum(axis=1) / cnt_l.replace(0, np.nan) * 100)
    # only trust breadth once most of the index has enough history
    b50 = b50.where(cnt_s >= 0.8 * len(syms))
    b200 = b200.where(cnt_l >= 0.6 * len(syms))

    chg = px.pct_change(fill_method=None)
    adv = (chg > 0).sum(axis=1)
    dec = (chg < 0).sum(axis=1)

    nifty_sma = nifty.rolling(ns, min_periods=ns).mean()
    nifty_above = nifty > nifty_sma
    nifty_sma_rising = nifty_sma > nifty_sma.shift(10)

    md = int(bc["momentum_days"])
    d5 = b50 - b50.shift(md)

    # breadth thrust: from <= thrust_from to >= thrust_to inside thrust_window sessions
    tw = int(bc["thrust_window"])
    roll_min = b50.rolling(tw + 1, min_periods=2).min()
    thrust_fire = (b50 >= bc["thrust_to"]) & (roll_min <= bc["thrust_from"])
    thrust_fire = thrust_fire & ~thrust_fire.shift(1, fill_value=False)
    thrust_recent = thrust_fire.astype(float).rolling(5, min_periods=1).max() > 0

    hist_n = int(cfg["dashboard"]["history_sessions"])
    valid_idx = b50.dropna().index
    hist_idx = valid_idx[-hist_n:]

    regimes = []
    for d in hist_idx:
        dd = d5.get(d)
        regimes.append(classify_regime(
            float(b50[d]), float(dd) if dd == dd else 0.0,
            bool(nifty_above.get(d, False)), bool(thrust_recent.get(d, False)), cfg))

    # A mode must hold for 2 sessions to be "confirmed" (stops one-day flip-flops).
    # A breadth thrust is a one-off event and is confirmed immediately.
    raw_regimes = regimes
    regimes = []
    for i, rg in enumerate(raw_regimes):
        if i == 0 or rg == "THRUST" or rg == raw_regimes[i - 1]:
            regimes.append(rg)
        else:
            regimes.append(regimes[-1])

    asof = valid_idx[-1]
    t = asof
    m_b50 = float(b50[t])
    m_d5 = float(d5[t]) if d5[t] == d5[t] else 0.0
    m_b200 = float(b200[t]) if b200[t] == b200[t] else float("nan")
    regime = regimes[-1]
    score = swing_score(m_b50, m_d5, m_b200, bool(nifty_above[t]), bool(nifty_sma_rising[t]), cfg)

    # ---------------- per-stock metrics ----------------
    rs_n = int(lc["rs_lookback_days"])
    slope_n = int(lc["slope_days"])
    ret_1m = px.iloc[-1] / px.iloc[-22] - 1 if len(px) > 22 else px.iloc[-1] * np.nan
    ret_3m = px.iloc[-1] / px.iloc[-(rs_n + 1)] - 1 if len(px) > rs_n else px.iloc[-1] * np.nan
    n_ret_1m = nifty.iloc[-1] / nifty.iloc[-22] - 1
    n_ret_3m = nifty.iloc[-1] / nifty.iloc[-(rs_n + 1)] - 1
    high_52 = px.rolling(252, min_periods=120).max().iloc[-1]
    last = px.iloc[-1]
    prev = px.iloc[-2]
    s50 = sma_s.iloc[-1]
    s200 = sma_l.iloc[-1]
    slope = sma_s.iloc[-1] / sma_s.iloc[-(slope_n + 1)] - 1
    vs50 = last / s50 - 1
    vs200 = last / s200 - 1
    off_high = last / high_52 - 1
    rs3 = ret_3m - n_ret_3m

    # days since crossing above the 50 DMA
    fresh_days = int(lc["fresh_cross_days"])
    recent_above = above_s.iloc[-(fresh_days + 1):]
    fresh_cross = recent_above.iloc[-1] & (~recent_above.iloc[:-1]).any()

    leader = pd.Series(True, index=syms)
    if lc.get("require_above_50dma", True):
        leader &= (last > s50).fillna(False)
    if lc.get("require_50_above_200", True):
        leader &= (s50 > s200).fillna(False)
    leader &= (slope > 0).fillna(False)
    leader &= (rs3 > 0).fillna(False)
    leader &= (off_high >= -lc["max_off_52w_high_pct"] / 100).fillna(False)
    extended = (vs50 > lc["extended_above_50dma_pct"] / 100).fillna(False)
    # looser "hold" rule so a watchlist stock isn't dropped on a one-day wobble
    hold_ok = (last > s50 * (1 - lc.get("exit_buffer_pct", 1) / 100)).fillna(False) & (s50 > s200).fillna(False)

    lscore = 100 * (0.45 * _pct_rank(rs3) + 0.30 * _pct_rank(off_high) + 0.25 * _pct_rank(slope))
    lscore = lscore - 10 * extended.astype(float)

    stocks = []
    for s in syms:
        stocks.append({
            "symbol": s,
            "name": meta[s]["name"],
            "sector": meta[s]["sector"],
            "sector_short": short_sector(meta[s]["sector"]),
            "close": _r(last[s]),
            "chg_1d": _r((last[s] / prev[s] - 1) * 100),
            "sma50": _r(s50[s]),
            "sma200": _r(s200[s]),
            "vs50": _r(vs50[s] * 100),
            "vs200": _r(vs200[s] * 100),
            "slope50": _r(slope[s] * 100),
            "ret_1m": _r(ret_1m[s] * 100),
            "ret_3m": _r(ret_3m[s] * 100),
            "rs_3m": _r(rs3[s] * 100),
            "off_high": _r(off_high[s] * 100),
            "above50": bool(above_s[s].iloc[-1]),
            "above200": bool(above_l[s].iloc[-1]),
            "leader": bool(leader[s]),
            "hold_ok": bool(hold_ok[s]),
            "extended": bool(extended[s]),
            "fresh_cross": bool(fresh_cross[s]),
            "score": _r(lscore[s], 1),
        })
    by_sym = {x["symbol"]: x for x in stocks}

    # ---------------- sectors ----------------
    sector_names = sorted({meta[s]["sector"] for s in syms})
    sectors = []
    for sec in sector_names:
        members = [s for s in syms if meta[s]["sector"] == sec]
        a_s = above_s[members]
        v_s = valid_s[members]
        sec_b50 = (a_s.sum(axis=1) / v_s.sum(axis=1).replace(0, np.nan) * 100)
        sec_b200 = (above_l[members].sum(axis=1) / valid_l[members].sum(axis=1).replace(0, np.nan) * 100)
        r1 = float(np.nanmean([ret_1m[m] for m in members]))
        rs1 = r1 - float(n_ret_1m)
        now = float(sec_b50.iloc[-1]) if sec_b50.iloc[-1] == sec_b50.iloc[-1] else 0.0
        w1 = sec_b50.iloc[-6] if len(sec_b50) > 6 else np.nan
        m1 = sec_b50.iloc[-22] if len(sec_b50) > 22 else np.nan
        # small sectors (1-2 stocks) are shrunk toward the market so they don't top the table on one stock
        n_valid = int(v_s.iloc[-1].sum())
        adj = (int(a_s.iloc[-1].sum()) + m_b50 / 100) / (n_valid + 1) * 100 if n_valid else 0.0
        sscore = 0.6 * adj + 0.4 * float(np.clip(50 + rs1 * 500, 0, 100))
        good = now >= sc["min_breadth_pct"]
        hold = now >= sc.get("exit_breadth_pct", sc["min_breadth_pct"] - 15)
        if now >= 60 and rs1 >= 0:
            status = "Leading"
        elif now >= 60:
            status = "Strong"
        elif w1 == w1 and now > w1:
            status = "Improving"
        elif now < 40:
            status = "Weak"
        else:
            status = "Mixed"
        sectors.append({
            "name": sec,
            "short": short_sector(sec),
            "n": len(members),
            "above": int(above_s[members].iloc[-1].sum()),
            "b50": _r(now, 1),
            "b50_1w": _r(w1, 1),
            "b50_1m": _r(m1, 1),
            "b200": _r(sec_b200.iloc[-1], 1),
            "ret_1m": _r(r1 * 100),
            "rs_1m": _r(rs1 * 100),
            "score": _r(sscore, 1),
            "good": bool(good),
            "hold": bool(hold),
            "status": status,
            "spark": [_r(v, 0) for v in sec_b50.iloc[-60:].tolist()],
            "members": sorted(members, key=lambda m: -(by_sym[m]["score"] or 0)),
        })
    sectors.sort(key=lambda x: -x["score"])
    for i, sct in enumerate(sectors, 1):
        sct["rank"] = i

    watchlist, by_sector = build_watchlist(stocks, sectors, previous=None)

    hist_regime_flags = [REGIMES[r]["tone"] for r in regimes]
    market = {
        "b50": _r(m_b50, 1),
        "above50": int(above_s.loc[t].sum()),
        "valid50": int(cnt_s[t]),
        "b200": _r(m_b200, 1),
        "above200": int(above_l.loc[t].sum()),
        "valid200": int(cnt_l[t]),
        "d5": _r(m_d5, 1),
        "b50_1m": _r(b50.iloc[-22], 1) if len(b50) > 22 else None,
        "adv": int(adv[t]),
        "dec": int(dec[t]),
        "nifty": _r(nifty[t]),
        "nifty_chg": _r((nifty.iloc[-1] / nifty.iloc[-2] - 1) * 100),
        "nifty_sma50": _r(nifty_sma[t]),
        "nifty_vs50": _r((nifty[t] / nifty_sma[t] - 1) * 100),
        "nifty_above50": bool(nifty_above[t]),
        "nifty_sma_rising": bool(nifty_sma_rising[t]),
        "nifty_ret_1m": _r(n_ret_1m * 100),
        "nifty_ret_3m": _r(n_ret_3m * 100),
        "score": _r(score, 0),
        "regime": regime,
        "regime_label": REGIMES[regime]["label"],
        "regime_tone": REGIMES[regime]["tone"],
        "advice": REGIMES[regime]["advice"],
        "thrust_today": bool(thrust_fire[t]),
        "regime_raw": raw_regimes[-1],
        "regime_raw_label": REGIMES[raw_regimes[-1]]["label"],
        "pending": raw_regimes[-1] != regime,
    }

    history = {
        "dates": [d.strftime("%Y-%m-%d") for d in hist_idx],
        "b50": [_r(b50[d], 1) for d in hist_idx],
        "b200": [_r(b200[d], 1) for d in hist_idx],
        "nifty": [_r(nifty[d], 1) for d in hist_idx],
        "regime": regimes,
        "tone": hist_regime_flags,
        "thrust": [d.strftime("%Y-%m-%d") for d in hist_idx if bool(thrust_fire.get(d, False))],
    }

    return {
        "asof": t.strftime("%Y-%m-%d"),
        "market": market,
        "history": history,
        "sectors": sectors,
        "stocks": stocks,
        "watchlist": watchlist,
        "by_sector": by_sector,
        "regimes": {k: {"label": v["label"], "tone": v["tone"]} for k, v in REGIMES.items()},
    }
