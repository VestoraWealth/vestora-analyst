"""Fundamental screen for stocks that enter the Vestora watchlist.

Criteria (all editable in config.yaml):
  * Debt / Equity below 0.5 (skipped for banks, NBFCs and insurers)
  * Sales growth, 3-year CAGR, 15% or more
  * Promoter pledge = 0%
  * Profitable (net profit > 0 in the latest two years)
  * Profit growth, 3-year CAGR, 15% or more
  * Piotroski F-score 6 or more (i.e. above 5)
  * Market cap above Rs 10,000 crore

Financial statements come from Yahoo Finance (annual reports, up to 4 years).
Promoter pledge comes from NSE's pledge disclosures (best effort).
"""
from __future__ import annotations

import logging
import math
import re
from urllib.parse import quote

from .universe import BROWSER_HEADERS, yahoo_symbol

log = logging.getLogger("vestora")

CRORE = 1e7

ROWS = {
    "revenue": ["Total Revenue", "Operating Revenue", "Revenue"],
    "net_income": ["Net Income", "Net Income Common Stockholders",
                   "Net Income From Continuing Operation Net Minority Interest"],
    "gross_profit": ["Gross Profit"],
    "total_assets": ["Total Assets"],
    "current_assets": ["Current Assets"],
    "current_liabilities": ["Current Liabilities"],
    "long_term_debt": ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"],
    "total_debt": ["Total Debt"],
    "equity": ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"],
    "shares": ["Ordinary Shares Number", "Share Issued"],
    "ocf": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
}


def _num(x):
    try:
        v = float(x)
        return None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return None


def _series(df, names) -> list:
    """Values of the first matching row, newest year first."""
    if df is None or getattr(df, "empty", True):
        return []
    for n in names:
        if n in df.index:
            row = df.loc[n]
            try:
                row = row.sort_index(ascending=False)
            except Exception:
                pass
            return [_num(v) for v in row.tolist()]
    return []


def extract_raw(info: dict, inc, bs, cf) -> dict:
    raw = {
        "revenue": _series(inc, ROWS["revenue"]),
        "net_income": _series(inc, ROWS["net_income"]),
        "gross_profit": _series(inc, ROWS["gross_profit"]),
        "total_assets": _series(bs, ROWS["total_assets"]),
        "current_assets": _series(bs, ROWS["current_assets"]),
        "current_liabilities": _series(bs, ROWS["current_liabilities"]),
        "long_term_debt": _series(bs, ROWS["long_term_debt"]),
        "total_debt": _series(bs, ROWS["total_debt"]),
        "equity": _series(bs, ROWS["equity"]),
        "shares": _series(bs, ROWS["shares"]),
        "ocf": _series(cf, ROWS["ocf"]),
        "market_cap": _num((info or {}).get("marketCap")),
        "de_info": _num((info or {}).get("debtToEquity")),
        "peg_info": _num((info or {}).get("trailingPegRatio") or (info or {}).get("pegRatio")),
        "pe": _num((info or {}).get("trailingPE")),
        "price": _num((info or {}).get("currentPrice") or (info or {}).get("regularMarketPrice")),
        "shares_out": _num((info or {}).get("sharesOutstanding")),
    }
    # If a company has no debt, Yahoo often omits the row entirely.
    if not raw["long_term_debt"] and raw["total_assets"] and not raw["total_debt"]:
        raw["long_term_debt"] = [0.0] * len(raw["total_assets"])
    return raw


def _g(raw, key, i):
    arr = raw.get(key) or []
    return arr[i] if i < len(arr) else None


def _div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


def _avg(a, b):
    if a is None:
        return None
    if b is None:
        return a
    return (a + b) / 2


def piotroski(raw: dict) -> dict:
    """Standard 9-point Piotroski F-score from two consecutive annual reports.

    Each test is True / False, or None when the data is not reported
    (e.g. banks have no 'gross profit' or 'current ratio').
    """
    ni0, ni1 = _g(raw, "net_income", 0), _g(raw, "net_income", 1)
    ta0, ta1, ta2 = (_g(raw, "total_assets", i) for i in range(3))
    ocf0 = _g(raw, "ocf", 0)
    a0, a1 = _avg(ta0, ta1), _avg(ta1, ta2)
    roa0, roa1 = _div(ni0, a0), _div(ni1, a1)

    ltd0, ltd1 = _g(raw, "long_term_debt", 0), _g(raw, "long_term_debt", 1)
    lev0, lev1 = _div(ltd0, a0), _div(ltd1, a1)
    cr0 = _div(_g(raw, "current_assets", 0), _g(raw, "current_liabilities", 0))
    cr1 = _div(_g(raw, "current_assets", 1), _g(raw, "current_liabilities", 1))
    sh0, sh1 = _g(raw, "shares", 0), _g(raw, "shares", 1)
    gm0 = _div(_g(raw, "gross_profit", 0), _g(raw, "revenue", 0))
    gm1 = _div(_g(raw, "gross_profit", 1), _g(raw, "revenue", 1))
    at0 = _div(_g(raw, "revenue", 0), a0)
    at1 = _div(_g(raw, "revenue", 1), a1)

    def t(cond_inputs, fn):
        return None if any(v is None for v in cond_inputs) else bool(fn())

    tests = {
        "Return on assets positive": t([roa0], lambda: roa0 > 0),
        "Operating cash flow positive": t([ocf0], lambda: ocf0 > 0),
        "Return on assets improving": t([roa0, roa1], lambda: roa0 > roa1),
        "Cash flow above net profit": t([ocf0, ni0], lambda: ocf0 > ni0),
        "Long-term debt ratio falling": t([lev0, lev1], lambda: lev0 <= lev1),
        "Current ratio improving": t([cr0, cr1], lambda: cr0 > cr1),
        "No new shares issued": t([sh0, sh1], lambda: sh0 <= sh1 * 1.001),
        "Gross margin improving": t([gm0, gm1], lambda: gm0 > gm1),
        "Asset turnover improving": t([at0, at1], lambda: at0 > at1),
    }
    avail = [v for v in tests.values() if v is not None]
    return {"score": sum(avail), "available": len(avail), "tests": tests}


def cagr(new, old, years):
    if new is None or old is None or old <= 0 or new <= 0:
        return None
    return (new / old) ** (1 / years) - 1


# ---------------------------------------------------------------------------
# Promoter pledge (NSE)
# ---------------------------------------------------------------------------
_PLEDGE_KEY = re.compile(r"(pledg|encumb)", re.I)
_PCT_KEY = re.compile(r"(per|pct|%)", re.I)


def nse_session():
    import requests
    s = requests.Session()
    h = dict(BROWSER_HEADERS, Accept="text/html,application/json,*/*")
    try:
        s.get("https://www.nseindia.com", headers=h, timeout=15)
        s.get("https://www.nseindia.com/companies-listing/corporate-filings-pledged-data", headers=h, timeout=15)
    except Exception as exc:
        log.warning("NSE session warm-up failed: %s", exc)
    return s


def parse_pledge_payload(payload) -> float | None:
    """Promoter pledge % from NSE's pledge-data JSON. Tolerant of field-name changes.

    Returns 0.0 when NSE has no pledge filings for the company, None when unknown.
    """
    if payload is None:
        return None
    records = payload.get("data") if isinstance(payload, dict) else payload
    if records is None:
        return None
    if isinstance(records, list) and len(records) == 0:
        return 0.0
    rec = records[0] if isinstance(records, list) else records
    if not isinstance(rec, dict):
        return None
    vals = []
    for k, v in rec.items():
        if _PLEDGE_KEY.search(k) and _PCT_KEY.search(k):
            n = _num(str(v).replace("%", "").replace(",", "")) if v not in (None, "", "-") else None
            if n is not None and 0 <= n <= 100:
                vals.append(n)
    return max(vals) if vals else None


def fetch_pledge(symbol: str, session) -> tuple[float | None, str]:
    if session is None:
        return None, "not checked"
    url = f"https://www.nseindia.com/api/corporate-pledgedata?index=equities&symbol={quote(symbol)}"
    try:
        r = session.get(url, headers=dict(BROWSER_HEADERS, Accept="application/json",
                                          Referer="https://www.nseindia.com/companies-listing/corporate-filings-pledged-data"),
                        timeout=15)
        if r.status_code != 200:
            return None, f"NSE returned {r.status_code}"
        return parse_pledge_payload(r.json()), "NSE pledge disclosures"
    except Exception as exc:
        return None, f"NSE unavailable ({type(exc).__name__})"


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def evaluate(raw: dict, sector: str, cfg: dict, pledge: float | None = None, pledge_src: str = "") -> dict:
    fc = cfg["fundamentals"]
    checks = []

    def add(key, label, value, display, status, rule):
        checks.append({"key": key, "label": label, "value": value, "display": display,
                       "status": status, "rule": rule})

    # 1. Debt / Equity
    rule = f"below {fc['max_debt_to_equity']}"
    if sector in (fc.get("skip_debt_check_sectors") or []):
        add("debt", "Debt / Equity", None, "N/A (lender)", "na", rule)
    else:
        de = _div(_g(raw, "total_debt", 0), _g(raw, "equity", 0))
        if de is None and raw.get("de_info") is not None:
            de = raw["de_info"] / 100.0
        if de is None:
            add("debt", "Debt / Equity", None, "no data", "check", rule)
        else:
            add("debt", "Debt / Equity", round(de, 2), f"{de:.2f}",
                "pass" if de < fc["max_debt_to_equity"] else "fail", rule)

    # 2. Sales growth 3Y CAGR
    rule = f"{fc['min_sales_cagr_3y_pct']}%+"
    g3 = cagr(_g(raw, "revenue", 0), _g(raw, "revenue", 3), 3)
    if g3 is None:
        g2 = cagr(_g(raw, "revenue", 0), _g(raw, "revenue", 2), 2)
        if g2 is None:
            add("sales", "Sales growth (3Y CAGR)", None, "no data", "check", rule)
        else:
            add("sales", "Sales growth (3Y CAGR)", round(g2 * 100, 1), f"{g2*100:.1f}% (2Y)",
                "pass" if g2 * 100 >= fc["min_sales_cagr_3y_pct"] else "fail", rule)
    else:
        add("sales", "Sales growth (3Y CAGR)", round(g3 * 100, 1), f"{g3*100:.1f}%",
            "pass" if g3 * 100 >= fc["min_sales_cagr_3y_pct"] else "fail", rule)

    # 3. Promoter pledge
    rule = "0%"
    if not fc.get("require_zero_pledge", True):
        add("pledge", "Promoter pledge", pledge, "not required", "na", rule)
    elif pledge is None:
        add("pledge", "Promoter pledge", None, "verify on NSE", "check", rule)
    else:
        add("pledge", "Promoter pledge", round(pledge, 2), f"{pledge:.2f}%",
            "pass" if pledge <= 0.0001 else "fail", rule)

    # 4. Profitable
    ni0, ni1 = _g(raw, "net_income", 0), _g(raw, "net_income", 1)
    rule = "net profit > 0"
    if ni0 is None:
        add("profit", "Profitable", None, "no data", "check", rule)
    else:
        ok = ni0 > 0 and (ni1 is None or ni1 > 0)
        add("profit", "Profitable", round(ni0 / CRORE, 0),
            f"₹{ni0/CRORE:,.0f} Cr profit" if ni0 > 0 else f"Loss ₹{abs(ni0)/CRORE:,.0f} Cr",
            "pass" if ok else "fail", rule)

    # 5. Profit growth (3-year CAGR of net profit)
    rule = f"{fc['min_profit_cagr_3y_pct']}%+"
    label = "Profit growth (3Y CAGR)"
    ni3 = _g(raw, "net_income", 3)
    if ni0 is not None and ni0 <= 0:
        add("pgrowth", label, None, "loss in latest year", "fail", rule)
    elif cagr(ni0, ni3, 3) is not None:
        p3 = cagr(ni0, ni3, 3)
        add("pgrowth", label, round(p3 * 100, 1), f"{p3*100:.1f}%",
            "pass" if p3 * 100 >= fc["min_profit_cagr_3y_pct"] else "fail", rule)
    elif ni3 is not None and ni3 <= 0:
        add("pgrowth", label, None, "was loss-making 3 yrs ago", "check", rule)
    else:
        p2 = cagr(ni0, _g(raw, "net_income", 2), 2)
        if p2 is None:
            add("pgrowth", label, None, "no data", "check", rule)
        else:
            add("pgrowth", label, round(p2 * 100, 1), f"{p2*100:.1f}% (2Y)",
                "pass" if p2 * 100 >= fc["min_profit_cagr_3y_pct"] else "fail", rule)

    # 6. Piotroski
    pf = piotroski(raw)
    rule = f"{fc['min_piotroski']}+ of 9"
    if pf["available"] < 5:
        add("piotroski", "Piotroski score", None, "not enough data", "check", rule)
    else:
        need = math.ceil(fc["min_piotroski"] * pf["available"] / 9)
        disp = f"{pf['score']}/9" if pf["available"] == 9 else f"{pf['score']}/{pf['available']} (adj.)"
        add("piotroski", "Piotroski score", pf["score"], disp,
            "pass" if pf["score"] >= need else "fail", rule)

    # 7. Market cap
    rule = f"above ₹{fc['min_market_cap_cr']:,} Cr"
    mc = raw.get("market_cap")
    if mc is None and raw.get("price") and raw.get("shares_out"):
        mc = raw["price"] * raw["shares_out"]
    if mc is None:
        add("mcap", "Market cap", None, "no data", "check", rule)
    else:
        cr = mc / CRORE
        disp = f"₹{cr/1e5:.2f} L Cr" if cr >= 1e5 else f"₹{cr:,.0f} Cr"
        add("mcap", "Market cap", round(cr, 0), disp,
            "pass" if cr > fc["min_market_cap_cr"] else "fail", rule)

    passed = sum(c["status"] == "pass" for c in checks)
    failed = sum(c["status"] == "fail" for c in checks)
    to_check = sum(c["status"] == "check" for c in checks)
    applicable = passed + failed + to_check
    if failed == 0 and to_check == 0:
        verdict = "STRONG"
    elif failed == 0:
        verdict = "STRONG*"
    elif failed <= 2:
        verdict = "PARTIAL"
    else:
        verdict = "WEAK"
    return {
        "checks": checks,
        "passed": passed,
        "failed": failed,
        "to_check": to_check,
        "applicable": applicable,
        "verdict": verdict,
        "piotroski_tests": pf["tests"],
        "pledge_source": pledge_src,
    }


def fetch_fundamentals(symbol: str, sector: str, cfg: dict, nse=None) -> dict:
    import yfinance as yf

    tk = yf.Ticker(yahoo_symbol(symbol))

    def safe(fn):
        try:
            return fn()
        except Exception as exc:
            log.warning("  %s: %s", symbol, exc)
            return None

    info = safe(lambda: tk.info) or {}
    inc = safe(lambda: tk.income_stmt)
    bs = safe(lambda: tk.balance_sheet)
    cf = safe(lambda: tk.cashflow)
    raw = extract_raw(info, inc, bs, cf)

    overrides = cfg["fundamentals"].get("pledge_overrides") or {}
    if symbol in overrides:
        pledge, src = float(overrides[symbol]), "manual override (config.yaml)"
    else:
        pledge, src = fetch_pledge(symbol, nse)
    return evaluate(raw, sector, cfg, pledge, src)
