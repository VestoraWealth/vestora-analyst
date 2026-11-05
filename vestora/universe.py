"""Nifty 50 constituents.

The live list is downloaded from NSE / Nifty Indices on every run, so index
changes are picked up automatically. If both downloads fail, the built-in list
below (current as of the 30 Sep 2026 rebalance: BSE in, Wipro out) is used.
"""
from __future__ import annotations

import csv
import io
import logging

log = logging.getLogger("vestora")

CONSTITUENT_URLS = [
    "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
]

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# (symbol, company name, NSE industry/sector)
FALLBACK_NIFTY50 = [
    ("ADANIENT", "Adani Enterprises", "Metals & Mining"),
    ("ADANIPORTS", "Adani Ports & SEZ", "Services"),
    ("APOLLOHOSP", "Apollo Hospitals", "Healthcare"),
    ("ASIANPAINT", "Asian Paints", "Consumer Durables"),
    ("AXISBANK", "Axis Bank", "Financial Services"),
    ("BAJAJ-AUTO", "Bajaj Auto", "Automobile and Auto Components"),
    ("BAJFINANCE", "Bajaj Finance", "Financial Services"),
    ("BAJAJFINSV", "Bajaj Finserv", "Financial Services"),
    ("BEL", "Bharat Electronics", "Capital Goods"),
    ("BHARTIARTL", "Bharti Airtel", "Telecommunication"),
    ("BSE", "BSE Ltd", "Financial Services"),
    ("CIPLA", "Cipla", "Healthcare"),
    ("COALINDIA", "Coal India", "Oil Gas & Consumable Fuels"),
    ("DRREDDY", "Dr. Reddy's Laboratories", "Healthcare"),
    ("EICHERMOT", "Eicher Motors", "Automobile and Auto Components"),
    ("ETERNAL", "Eternal", "Consumer Services"),
    ("GRASIM", "Grasim Industries", "Construction Materials"),
    ("HCLTECH", "HCLTech", "Information Technology"),
    ("HDFCBANK", "HDFC Bank", "Financial Services"),
    ("HDFCLIFE", "HDFC Life", "Financial Services"),
    ("HINDALCO", "Hindalco Industries", "Metals & Mining"),
    ("HINDUNILVR", "Hindustan Unilever", "Fast Moving Consumer Goods"),
    ("ICICIBANK", "ICICI Bank", "Financial Services"),
    ("INDIGO", "InterGlobe Aviation (IndiGo)", "Services"),
    ("INFY", "Infosys", "Information Technology"),
    ("ITC", "ITC", "Fast Moving Consumer Goods"),
    ("JIOFIN", "Jio Financial Services", "Financial Services"),
    ("JSWSTEEL", "JSW Steel", "Metals & Mining"),
    ("KOTAKBANK", "Kotak Mahindra Bank", "Financial Services"),
    ("LT", "Larsen & Toubro", "Construction"),
    ("M&M", "Mahindra & Mahindra", "Automobile and Auto Components"),
    ("MARUTI", "Maruti Suzuki", "Automobile and Auto Components"),
    ("MAXHEALTH", "Max Healthcare", "Healthcare"),
    ("NESTLEIND", "Nestle India", "Fast Moving Consumer Goods"),
    ("NTPC", "NTPC", "Power"),
    ("ONGC", "ONGC", "Oil Gas & Consumable Fuels"),
    ("POWERGRID", "Power Grid", "Power"),
    ("RELIANCE", "Reliance Industries", "Oil Gas & Consumable Fuels"),
    ("SBILIFE", "SBI Life Insurance", "Financial Services"),
    ("SBIN", "State Bank of India", "Financial Services"),
    ("SHRIRAMFIN", "Shriram Finance", "Financial Services"),
    ("SUNPHARMA", "Sun Pharma", "Healthcare"),
    ("TATACONSUM", "Tata Consumer Products", "Fast Moving Consumer Goods"),
    ("TATASTEEL", "Tata Steel", "Metals & Mining"),
    ("TCS", "Tata Consultancy Services", "Information Technology"),
    ("TECHM", "Tech Mahindra", "Information Technology"),
    ("TITAN", "Titan Company", "Consumer Durables"),
    ("TMPV", "Tata Motors Passenger Vehicles", "Automobile and Auto Components"),
    ("TRENT", "Trent", "Consumer Services"),
    ("ULTRACEMCO", "UltraTech Cement", "Construction Materials"),
]

# Shorter labels for the dashboard / WhatsApp
SECTOR_SHORT = {
    "Financial Services": "Financials",
    "Information Technology": "IT",
    "Automobile and Auto Components": "Auto",
    "Fast Moving Consumer Goods": "FMCG",
    "Oil Gas & Consumable Fuels": "Oil & Gas",
    "Oil, Gas & Consumable Fuels": "Oil & Gas",
    "Metals & Mining": "Metals",
    "Construction Materials": "Cement & Materials",
    "Consumer Durables": "Consumer Durables",
    "Consumer Services": "Consumer Services",
    "Capital Goods": "Capital Goods",
    "Telecommunication": "Telecom",
    "Healthcare": "Healthcare",
    "Power": "Power",
    "Construction": "Construction",
    "Services": "Services",
}


def short_sector(name: str) -> str:
    return SECTOR_SHORT.get(name, name)


def yahoo_symbol(nse_symbol: str) -> str:
    return f"{nse_symbol}.NS"


def _parse_csv(text: str) -> list[dict]:
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    for r in reader:
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        sym = r.get("Symbol")
        if not sym:
            continue
        rows.append({
            "symbol": sym,
            "name": r.get("Company Name", sym),
            "sector": r.get("Industry", "Other").replace(",", ""),
        })
    return rows


def fallback_constituents() -> list[dict]:
    return [{"symbol": s, "name": n, "sector": sec} for s, n, sec in FALLBACK_NIFTY50]


def load_constituents(session=None) -> tuple[list[dict], str]:
    """Return (constituents, source). Never raises."""
    if session is not None:
        for url in CONSTITUENT_URLS:
            try:
                resp = session.get(url, headers=BROWSER_HEADERS, timeout=20)
                if resp.status_code == 200 and "Symbol" in resp.text[:500]:
                    rows = _parse_csv(resp.text)
                    if 45 <= len(rows) <= 55:
                        log.info("Constituents: %d from %s", len(rows), url)
                        return rows, url
                log.warning("Constituent download from %s returned %s", url, resp.status_code)
            except Exception as exc:  # network problems must never stop the run
                log.warning("Constituent download failed (%s): %s", url, exc)
    log.warning("Using built-in Nifty 50 list")
    return fallback_constituents(), "built-in list (Sep 2026)"
