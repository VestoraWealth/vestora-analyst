"""Checks for the VESTORA ANALYST calculations. Run: python -m pytest tests"""
import sys
import types
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vestora import fundamentals as fu  # noqa: E402
from vestora import notify  # noqa: E402
from vestora.demo import synthetic_fundamentals, synthetic_prices  # noqa: E402
from vestora.market import analyse, build_watchlist, classify_regime, swing_score  # noqa: E402
from vestora.universe import _parse_csv, fallback_constituents  # noqa: E402

CFG = yaml.safe_load((ROOT / "config.yaml").read_text())


# ---------------------------------------------------------------- universe
def test_fallback_list_is_current_nifty50():
    cons = fallback_constituents()
    syms = {c["symbol"] for c in cons}
    assert len(cons) == 50 and len(syms) == 50
    assert "BSE" in syms and "WIPRO" not in syms  # Sep 2026 rebalance
    assert {"INDIGO", "MAXHEALTH", "ETERNAL", "JIOFIN", "TMPV"} <= syms


def test_parse_nse_csv():
    csv = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
           "Reliance Industries Ltd.,\"Oil, Gas & Consumable Fuels\",RELIANCE,EQ,INE002A01018\n"
           "BSE Ltd.,Financial Services,BSE,EQ,INE118H01025\n")
    rows = _parse_csv(csv)
    assert rows[0] == {"symbol": "RELIANCE", "name": "Reliance Industries Ltd.", "sector": "Oil Gas & Consumable Fuels"}
    assert rows[1]["sector"] == "Financial Services"


# ---------------------------------------------------------------- breadth
def _toy(n_days=320):
    """6 stocks, 2 sectors. A,B,C trend up; D,E,F trend down. Nifty up."""
    idx = pd.bdate_range("2025-01-01", periods=n_days)
    t = np.arange(n_days)
    up = 100 * np.exp(0.002 * t)
    down = 100 * np.exp(-0.002 * t)
    close = pd.DataFrame({"A": up, "B": up * 1.1, "C": up * 0.9, "D": down, "E": down * 1.2, "F": down * 0.8}, index=idx)
    nifty = pd.Series(up * 200, index=idx)
    cons = [{"symbol": s, "name": s, "sector": "Up Sector" if s in "ABC" else "Down Sector"} for s in "ABCDEF"]
    return close, nifty, cons


def test_breadth_counts_exactly():
    close, nifty, cons = _toy()
    r = analyse(close, nifty, cons, CFG)
    m = r["market"]
    assert (m["above50"], m["valid50"], m["b50"]) == (3, 6, 50.0)
    assert (m["above200"], m["valid200"], m["b200"]) == (3, 6, 50.0)
    secs = {s["name"]: s for s in r["sectors"]}
    assert secs["Up Sector"]["b50"] == 100.0 and secs["Up Sector"]["above"] == 3
    assert secs["Down Sector"]["b50"] == 0.0
    assert r["sectors"][0]["name"] == "Up Sector"  # ranked first


def test_breadth_matches_manual_rolling_mean():
    close, nifty = synthetic_prices(fallback_constituents(), end="2026-11-05")
    r = analyse(close, nifty, fallback_constituents(), CFG)
    sma = close.rolling(50).mean().iloc[-1]
    manual = int((close.iloc[-1] > sma).sum())
    assert r["market"]["above50"] == manual
    assert r["market"]["b50"] == round(manual / 50 * 100, 1)
    assert len(r["history"]["dates"]) == CFG["dashboard"]["history_sessions"]


def test_leaders_only_from_uptrending_stocks():
    close, nifty, cons = _toy()
    r = analyse(close, nifty, cons, CFG)
    leaders = {s["symbol"] for s in r["stocks"] if s["leader"]}
    assert leaders <= {"A", "B", "C"}
    assert not leaders & {"D", "E", "F"}


# ---------------------------------------------------------------- swing model
@pytest.mark.parametrize("b50,d5,nifty_above,thrust,expected", [
    (70, 4, True, False, "BEST"),
    (70, -8, True, False, "FADING"),
    (55, 3, True, False, "GOOD"),
    (90, 2, True, False, "OVERHEATED"),
    (35, -2, False, False, "AVOID"),
    (20, -1, False, False, "OVERSOLD"),
    (24, 12, False, False, "EARLY_REVERSAL"),
    (62, 30, True, True, "THRUST"),
    (45, 2, True, False, "NEUTRAL"),
])
def test_regime_rules(b50, d5, nifty_above, thrust, expected):
    assert classify_regime(b50, d5, nifty_above, thrust, CFG) == expected


def test_swing_score_bounds_and_order():
    good = swing_score(70, 10, 65, True, True, CFG)
    bad = swing_score(20, -10, 15, False, False, CFG)
    assert 0 <= bad < good <= 100
    assert swing_score(95, 0, 90, True, True, CFG) < swing_score(80, 0, 90, True, True, CFG) + 15


# ---------------------------------------------------------------- fundamentals
def _raw(**over):
    raw = {
        "revenue": [1500.0, 1300.0, 1100.0, 950.0],
        "net_income": [200.0, 160.0, 130.0, 100.0],
        "gross_profit": [600.0, 500.0, 420.0, 360.0],
        "total_assets": [2000.0, 1900.0, 1800.0, 1700.0],
        "current_assets": [800.0, 700.0, 650.0, 600.0],
        "current_liabilities": [400.0, 400.0, 400.0, 400.0],
        "long_term_debt": [100.0, 150.0, 160.0, 170.0],
        "total_debt": [300.0, 350.0, 380.0, 400.0],
        "equity": [1000.0, 900.0, 820.0, 760.0],
        "shares": [100.0, 100.0, 100.0, 100.0],
        "ocf": [260.0, 200.0, 150.0, 120.0],
        "market_cap": 3.0e12, "de_info": None, "peg_info": 1.2, "pe": None, "price": None, "shares_out": None,
    }
    raw.update(over)
    return raw


def test_piotroski_perfect_and_bank_style():
    full = fu.piotroski(_raw())
    assert full["available"] == 9 and full["score"] == 9
    bank = fu.piotroski(_raw(gross_profit=[], current_assets=[], current_liabilities=[]))
    assert bank["available"] == 7 and bank["score"] == 7
    worse = fu.piotroski(_raw(ocf=[150.0, 200.0, 150.0, 120.0], shares=[110.0, 100.0, 100.0, 100.0]))
    assert worse["tests"]["Cash flow above net profit"] is False
    assert worse["tests"]["No new shares issued"] is False
    assert worse["score"] == 7


def test_cagr():
    assert fu.cagr(1331.0, 1000.0, 3) == pytest.approx(0.10)
    assert fu.cagr(100.0, -5.0, 3) is None


def test_evaluate_all_pass():
    f = fu.evaluate(_raw(), "Automobile and Auto Components", CFG, pledge=0.0)
    st = {c["key"]: c["status"] for c in f["checks"]}
    assert st == {"debt": "pass", "sales": "pass", "pledge": "pass", "profit": "pass",
                  "pgrowth": "pass", "piotroski": "pass", "mcap": "pass"}
    assert f["verdict"] == "STRONG" and f["passed"] == 7


def test_evaluate_thresholds_and_special_cases():
    # D/E 0.6 fails; sales CAGR (1100/1000)^(1/3)-1 = 3.2% fails; pledge unknown -> check
    f = fu.evaluate(_raw(total_debt=[600.0], revenue=[1100.0, 1050.0, 1020.0, 1000.0]), "Metals & Mining", CFG, pledge=None)
    st = {c["key"]: c["status"] for c in f["checks"]}
    assert st["debt"] == "fail" and st["sales"] == "fail" and st["pledge"] == "check"
    # lenders: debt check not applied
    g = fu.evaluate(_raw(), "Financial Services", CFG, pledge=0.0)
    assert {c["key"]: c["status"] for c in g["checks"]}["debt"] == "na"
    # loss-making
    h = fu.evaluate(_raw(net_income=[-50.0, 20.0, 10.0, 5.0], peg_info=None), "Healthcare", CFG, pledge=0.0)
    hs = {c["key"]: c["status"] for c in h["checks"]}
    assert hs["profit"] == "fail" and hs["pgrowth"] == "fail"
    # profit CAGR (110/100)^(1/3)-1 = 3.2% fails; market cap below 10,000 Cr fails
    k = fu.evaluate(_raw(net_income=[110.0, 105.0, 102.0, 100.0], market_cap=9.0e10), "IT", CFG, pledge=0.0)
    ks = {c["key"]: c["status"] for c in k["checks"]}
    assert ks["pgrowth"] == "fail" and ks["mcap"] == "fail"
    # turnaround from a loss 3 years ago can't be measured -> flagged to check
    t = fu.evaluate(_raw(net_income=[200.0, 150.0, 80.0, -20.0]), "IT", CFG, pledge=0.0)
    assert {c["key"]: c["status"] for c in t["checks"]}["pgrowth"] == "check"


def test_pledge_payload_parsing():
    assert fu.parse_pledge_payload({"data": []}) == 0.0
    assert fu.parse_pledge_payload({"data": [{"comName": "X", "percSharesPledged": "12.5", "totPromoterHolding": "60"}]}) == 12.5
    assert fu.parse_pledge_payload({"data": [{"comName": "X", "encumbered_pct": "0.00"}]}) == 0.0
    assert fu.parse_pledge_payload({"data": [{"comName": "X", "promoterHolding": "55"}]}) is None
    assert fu.parse_pledge_payload(None) is None


def test_extract_raw_from_yahoo_shaped_frames():
    cols = pd.to_datetime(["2023-03-31", "2026-03-31", "2024-03-31", "2025-03-31"])  # unsorted on purpose
    inc = pd.DataFrame([[100, 160, 120, 140], [10, 20, 12, 16]], index=["Total Revenue", "Net Income"], columns=cols)
    bs = pd.DataFrame([[500, 650, 550, 600], [300, 390, 330, 360]], index=["Total Assets", "Stockholders Equity"], columns=cols)
    raw = fu.extract_raw({"marketCap": 5e11, "debtToEquity": 12.0}, inc, bs, None)
    assert raw["revenue"] == [160, 140, 120, 100]  # newest first
    assert raw["long_term_debt"] == [0.0] * 4      # no debt rows -> zero debt
    f = fu.evaluate(raw, "IT", CFG, pledge=0.0)
    assert {c["key"]: c["display"] for c in f["checks"]}["sales"] == "17.0%"  # (160/100)^(1/3) - 1 = 16.96%


# ---------------------------------------------------------------- watchlist & alerts
def test_watchlist_hold_rule_prevents_flip_flop():
    stocks = [
        {"symbol": "X", "sector": "S", "leader": True, "hold_ok": True, "score": 80},
        {"symbol": "Y", "sector": "S", "leader": False, "hold_ok": True, "score": 60},   # was listed, still holds
        {"symbol": "Z", "sector": "S", "leader": False, "hold_ok": False, "score": 50},  # was listed, broke 50 DMA
    ]
    sectors = [{"name": "S", "short": "S", "good": True, "hold": True, "status": "Leading", "b50": 70, "above": 2, "n": 3, "members": ["X", "Y", "Z"]}]
    wl, _ = build_watchlist(stocks, sectors, previous=["Y", "Z"])
    assert wl == ["X", "Y"]


def _result():
    cons = fallback_constituents()
    close, nifty = synthetic_prices(cons, end="2026-11-05")
    return analyse(close, nifty, cons, CFG), cons


def test_events_and_messages():
    res, cons = _result()
    assert notify.detect_events({}, res, cons, CFG) == [{"type": "welcome"}]
    prev = {"asof": "2026-11-04", "regime": "AVOID", "watchlist": ["ZZZ"] + res["watchlist"][1:],
            "constituents": [c["symbol"] for c in cons if c["symbol"] != "BSE"] + ["WIPRO"]}
    ev = notify.detect_events(prev, res, cons, CFG)
    types_ = [e["type"] for e in ev]
    assert "mode_change" in types_
    assert {"type": "new_stock", "symbol": res["watchlist"][0]} in ev
    assert {"type": "exits", "symbols": ["ZZZ"]} in ev
    assert {"type": "index_change", "added": ["BSE"], "removed": ["WIPRO"]} in ev
    fund = {s: synthetic_fundamentals(s, "IT", CFG) for s in res["watchlist"]}
    msgs = notify.compose(ev, res, fund, CFG, "https://example.github.io/vestora-analyst/")
    assert all(len(m["text"]) <= CFG["alerts"]["max_message_chars"] for m in msgs)
    new_msg = next(m for m in msgs if m["type"] == "new_stock")
    assert "Fundamentals:" in new_msg["text"] and "Piotroski" in new_msg["text"]
    assert msgs[-1]["type"] == "summary" and "Dashboard:" in msgs[-1]["text"]


def test_callmebot_request(monkeypatch):
    sent = {}

    class Resp:
        status_code = 200
        text = "Message queued. You will receive it in a few seconds."

    fake = types.SimpleNamespace(get=lambda url, params, timeout: (sent.update(url=url, **params), Resp())[1])
    monkeypatch.setitem(sys.modules, "requests", fake)
    monkeypatch.setenv("WHATSAPP_PHONE", "+919048595992")
    monkeypatch.setenv("CALLMEBOT_APIKEY", "123456")
    monkeypatch.setattr(notify.time, "sleep", lambda s: None)
    out = notify.deliver(["*VESTORA ANALYST* test"], dict(CFG, alerts=dict(CFG["alerts"], provider="callmebot")))
    assert out[0]["ok"] is True
    assert sent["url"] == "https://api.callmebot.com/whatsapp.php"
    assert sent["phone"] == "+919048595992" and sent["apikey"] == "123456"


# ---------------------------------------------------------------- download & full run
def test_fetch_prices_with_yahoo_shaped_download(monkeypatch):
    cons = fallback_constituents()
    close, nifty = synthetic_prices(cons, end="2026-11-05", days=300)
    tick = [f"{c['symbol']}.NS" for c in cons] + ["^NSEI"]
    data = pd.concat([close, nifty.rename("NIFTY")], axis=1)
    data.columns = tick
    data.loc[:, "TCS.NS"] = np.nan  # one ticker fails in the batch...
    frame = pd.concat({"Close": data, "Open": data}, axis=1)
    frame.index = frame.index.tz_localize("Asia/Kolkata")

    def download(tickers, **kw):
        if tickers == "TCS.NS":  # ...and succeeds on the single retry
            return pd.DataFrame({"Close": close["TCS"].values}, index=close.index)
        return frame

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=download))
    from vestora.market import fetch_prices
    got_close, got_nifty = fetch_prices([c["symbol"] for c in cons])
    assert got_close.shape[1] == 50 and "TCS" in got_close.columns
    assert got_close["TCS"].iloc[-1] == pytest.approx(close["TCS"].iloc[-1])
    assert got_nifty.iloc[-1] == pytest.approx(nifty.iloc[-1])
    assert got_close.index.tz is None


def test_run_once_is_idempotent_within_a_session(tmp_path):
    import run
    cons = fallback_constituents()
    close, nifty = synthetic_prices(cons, end="2026-11-05")
    fund_fn = lambda s, sec: synthetic_fundamentals(s, sec, CFG)  # noqa: E731
    today = date(2026, 11, 5)
    st1, _, _ = run.run_once(close.iloc[:-1], nifty.iloc[:-1], cons, {}, CFG, fund_fn, today=today, dry_run=True)
    st2, data, msgs = run.run_once(close, nifty, cons, st1, CFG, fund_fn, today=today, dry_run=True)
    assert msgs and msgs[-1]["type"] == "summary"
    assert set(data["fundamentals"]) == set(data["watchlist"])
    st3, data3, msgs3 = run.run_once(close, nifty, cons, st2, CFG, fund_fn, today=today, dry_run=True)
    assert msgs3 == [] and data3["watchlist"] == data["watchlist"] and st3["alerts_log"] == st2["alerts_log"]
    from vestora import dashboard
    out = dashboard.render(data, tmp_path)
    html = out.read_text()
    assert html.startswith("<!doctype html>") and "__VESTORA_DATA__" not in html and "__LOGO_DATA_URI__" not in html


def test_textmebot_request(monkeypatch):
    sent = {}

    class Resp:
        status_code = 200
        text = "Success! Message sent"

    fake = types.SimpleNamespace(get=lambda url, params, timeout: (sent.update(url=url, **params), Resp())[1])
    monkeypatch.setitem(sys.modules, "requests", fake)
    monkeypatch.setenv("WHATSAPP_PHONE", "+919048595992")
    monkeypatch.setenv("TEXTMEBOT_APIKEY", "abc123")
    monkeypatch.setattr(notify.time, "sleep", lambda s: None)
    cfg = dict(CFG, alerts=dict(CFG["alerts"], provider="textmebot"))
    out = notify.deliver(["*VESTORA ANALYST* test"], cfg)
    assert out[0]["ok"] is True
    assert sent["url"] == "https://api.textmebot.com/send.php"
    assert sent["recipient"] == "+919048595992" and sent["apikey"] == "abc123"
