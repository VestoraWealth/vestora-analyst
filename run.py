"""VESTORA ANALYST — Nifty 50 market-breadth analyser.

Usage
  python run.py                 normal run (downloads data, sends WhatsApp alerts)
  python run.py --dry-run       everything except sending WhatsApp (prints messages)
  python run.py --test-alerts   send one test message (WhatsApp + email) and stop
  python run.py --demo          simulated data, no internet needed, nothing sent
  python run.py --force         treat today as a new session even if already processed
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from vestora import dashboard, notify
from vestora.market import analyse, build_watchlist
from vestora.universe import fallback_constituents, load_constituents

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state" / "state.json"
DOCS = ROOT / "docs"
IST = timezone(timedelta(hours=5, minutes=30))

log = logging.getLogger("vestora")


def load_config(path: Path = ROOT / "config.yaml") -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_state(path: Path = STATE_FILE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


def dashboard_url() -> str:
    url = os.environ.get("DASHBOARD_URL", "").strip()
    if url:
        return url
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return ""


def run_once(close, nifty, constituents, state, cfg, fund_fn, *, today: date,
             dry_run: bool, force: bool = False, url: str = "", sources: dict | None = None):
    """One analyser pass. Returns (new_state, dashboard_data, messages)."""
    res = analyse(close, nifty, constituents, cfg)
    new_session = force or res["asof"] != state.get("asof")
    # re-apply watchlist with the previous list so holdings don't flip-flop
    prev_list = state.get("watchlist") if res["asof"] != state.get("asof") else state.get("prev_watchlist")
    res["watchlist"], res["by_sector"] = build_watchlist(res["stocks"], res["sectors"], prev_list)

    # ---- fundamentals for every watchlist stock (cached) ----
    fc = cfg["fundamentals"]
    cache = dict(state.get("fundamentals") or {})
    sector_of = {s["symbol"]: s["sector"] for s in res["stocks"]}
    for sym in res["watchlist"]:
        hit = cache.get(sym)
        fresh = False
        if hit and hit.get("checked"):
            try:
                age = (today - date.fromisoformat(hit["checked"])).days
                fresh = age < int(fc.get("cache_days", 7))
            except ValueError:
                pass
        if fresh:
            continue
        log.info("Fundamentals: checking %s", sym)
        try:
            f = fund_fn(sym, sector_of[sym])
            f["checked"] = today.isoformat()
            cache[sym] = f
        except Exception as exc:
            log.warning("Fundamentals failed for %s: %s", sym, exc)
    # keep cache small: watchlist + anything checked in the last 30 days
    keep = {}
    for k, v in cache.items():
        try:
            recent = (today - date.fromisoformat(v.get("checked", "2000-01-01"))).days <= 30
        except ValueError:
            recent = False
        if k in res["watchlist"] or recent:
            keep[k] = v
    cache = keep

    # ---- alerts ----
    messages, delivery = [], []
    if new_session:
        events = notify.detect_events(state, res, constituents, cfg)
        messages = notify.compose(events, res, cache, cfg, url, new_session=True)
        m0 = res["market"]
        subject = f"VESTORA ANALYST · {datetime.strptime(res['asof'], '%Y-%m-%d').strftime('%d %b')} · {m0['regime_label']}"
        if any(m["type"] == "new_stock" for m in messages):
            subject += " · new stock"
        delivery = notify.deliver([m["text"] for m in messages], cfg, dry_run=dry_run, subject=subject)
    else:
        log.info("No new trading session since %s — no alerts sent.", state.get("asof"))

    alerts_log = list(state.get("alerts_log") or [])
    for i, msg in enumerate(messages):
        sent = delivery[i]["ok"] if i < len(delivery) else False
        alerts_log.append({"date": res["asof"], "type": msg["type"], "title": msg["title"],
                           "text": msg["text"], "sent": sent})
    alerts_log = alerts_log[-80:]

    new_state = {
        "asof": res["asof"],
        "regime": res["market"]["regime"],
        "watchlist": res["watchlist"],
        "prev_watchlist": prev_list if res["asof"] == state.get("asof") else state.get("watchlist"),
        "constituents": [c["symbol"] for c in constituents],
        "fundamentals": cache,
        "alerts_log": alerts_log,
        "first_seen": {**(state.get("first_seen") or {}),
                       **{s: (state.get("first_seen") or {}).get(s, res["asof"]) for s in res["watchlist"]}},
    }
    # forget first_seen for stocks no longer listed
    new_state["first_seen"] = {k: v for k, v in new_state["first_seen"].items() if k in res["watchlist"]}

    data = dict(res)
    data["fundamentals"] = {s: cache[s] for s in res["watchlist"] if s in cache}
    data["first_seen"] = new_state["first_seen"]
    data["alerts"] = alerts_log[-40:][::-1]
    data["generated_at"] = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    data["sources"] = sources or {}
    data["dashboard_url"] = url
    data["settings"] = {
        "breadth": cfg["breadth"], "sectors": cfg["sectors"], "leaders": cfg["leaders"],
        "fundamentals": {k: v for k, v in cfg["fundamentals"].items() if k != "pledge_overrides"},
    }
    return new_state, data, messages


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VESTORA ANALYST")
    ap.add_argument("--demo", action="store_true", help="use simulated data (no internet)")
    ap.add_argument("--dry-run", action="store_true", help="don't send WhatsApp messages")
    ap.add_argument("--force", action="store_true", help="process even if no new session")
    ap.add_argument("--test-alerts", "--test-whatsapp", dest="test_alerts", action="store_true",
                    help="send a test message by WhatsApp and email, then exit")
    ap.add_argument("--replay", type=int, default=0, help="(demo) replay the last N sessions to build alert history")
    ap.add_argument("--out", default=str(DOCS), help="output folder for the dashboard")
    ap.add_argument("--state", default=str(STATE_FILE), help="state file")
    ap.add_argument("--fragment", default="", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    cfg = load_config()
    state_path = Path(args.state)
    state = load_state(state_path)
    today = datetime.now(IST).date()
    url = dashboard_url()

    if args.test_alerts:
        res = notify.deliver([f"*VESTORA ANALYST* ✅ Test message — your alerts are working.\n{url}".strip()],
                             cfg, subject="VESTORA ANALYST · test message")
        return 0 if res and res[0]["ok"] else 1

    if args.demo:
        from vestora.demo import synthetic_fundamentals, synthetic_prices
        constituents = fallback_constituents()
        close, nifty = synthetic_prices(constituents, end=today.isoformat())
        fund_fn = lambda s, sec: synthetic_fundamentals(s, sec, cfg)  # noqa: E731
        sources = {"prices": "SIMULATED DATA (demo mode)", "constituents": "built-in list", "demo": True}
        dry = True
    else:
        import requests
        from vestora.fundamentals import fetch_fundamentals, nse_session
        from vestora.market import fetch_prices
        sess = requests.Session()
        constituents, csrc = load_constituents(sess)
        close, nifty = fetch_prices([c["symbol"] for c in constituents])
        nse = nse_session()
        fund_fn = lambda s, sec: fetch_fundamentals(s, sec, cfg, nse)  # noqa: E731
        sources = {"prices": "Yahoo Finance (daily, adjusted)", "constituents": csrc,
                   "fundamentals": "Yahoo Finance annual reports; promoter pledge from NSE", "demo": False}
        dry = args.dry_run

    if args.replay and args.demo:
        state = {}
        for back in range(args.replay, 0, -1):
            state, _, _ = run_once(close.iloc[:-back], nifty.iloc[:-back], constituents, state, cfg, fund_fn,
                                   today=today, dry_run=True, url=url, sources=sources)

    new_state, data, messages = run_once(close, nifty, constituents, state, cfg, fund_fn, today=today,
                                         dry_run=dry, force=args.force, url=url, sources=sources)
    out = dashboard.render(data, Path(args.out), Path(args.fragment) if args.fragment else None)
    save_state(new_state, state_path)
    m = data["market"]
    log.info("Done. %s | above 50 DMA %s/%s (%.0f%%) | mode %s | watchlist %s | %d message(s) | dashboard %s",
             data["asof"], m["above50"], m["valid50"], m["b50"], m["regime_label"],
             ",".join(data["watchlist"]) or "-", len(messages), out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
