"""Alert detection, WhatsApp message text and delivery (CallMeBot or Twilio)."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime

log = logging.getLogger("vestora")

TONE_EMOJI = {"best": "🟢", "good": "🟡", "neutral": "⚪", "warn": "🟠", "bad": "🔴"}
STATUS_EMOJI = {"pass": "✅", "fail": "❌", "check": "⚠️", "na": "➖"}


def _fmt_price(x):
    return "—" if x is None else f"₹{x:,.2f}" if x < 1000 else f"₹{x:,.0f}"


def _signed(x, nd=1, suffix="%"):
    return "—" if x is None else f"{x:+.{nd}f}{suffix}"


def _date(d: str) -> str:
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%d %b %Y")
    except Exception:
        return d


# ---------------------------------------------------------------------------
# Event detection
# ---------------------------------------------------------------------------
def detect_events(prev: dict, res: dict, constituents: list[dict], cfg: dict) -> list[dict]:
    ac = cfg["alerts"]
    m = res["market"]
    events: list[dict] = []
    if not prev.get("asof"):
        return [{"type": "welcome"}]

    if ac.get("on_mode_change", True) and prev.get("regime") and prev["regime"] != m["regime"]:
        events.append({"type": "mode_change", "old": prev["regime"], "new": m["regime"]})

    if ac.get("on_breadth_thrust", True) and m.get("thrust_today"):
        events.append({"type": "thrust"})

    old_w = set(prev.get("watchlist") or [])
    new_w = res["watchlist"]
    if ac.get("on_new_stock", True):
        for s in new_w:
            if s not in old_w:
                events.append({"type": "new_stock", "symbol": s})
    if ac.get("on_stock_exit", True):
        exits = [s for s in (prev.get("watchlist") or []) if s not in set(new_w)]
        if exits:
            events.append({"type": "exits", "symbols": exits})

    if ac.get("on_index_change", True) and prev.get("constituents"):
        old_c = set(prev["constituents"])
        now_c = {c["symbol"] for c in constituents}
        added, removed = sorted(now_c - old_c), sorted(old_c - now_c)
        if added or removed:
            events.append({"type": "index_change", "added": added, "removed": removed})
    return events


# ---------------------------------------------------------------------------
# Message text
# ---------------------------------------------------------------------------
def _header(res):
    return f"*VESTORA ANALYST* · {_date(res['asof'])}"


def _breadth_lines(res):
    m = res["market"]
    lines = [
        f"Nifty 50 above 50 DMA: *{m['above50']}/{m['valid50']} ({m['b50']:.0f}%)* "
        f"{'▲' if (m['d5'] or 0) >= 0 else '▼'}{abs(m['d5'] or 0):.0f} pts in 5 days",
        f"Above 200 DMA: {m['above200']}/{m['valid200']} ({(m['b200'] or 0):.0f}%)",
        f"Swing score: *{m['score']:.0f}/100*",
        f"Nifty: {m['nifty']:,.0f} ({_signed(m['nifty_chg'], 2)}), "
        f"{'above' if m['nifty_above50'] else 'below'} 50 DMA",
    ]
    return lines


def _top_sectors(res, n=3):
    secs = [s for s in res["sectors"] if s["good"]][:n] or res["sectors"][:n]
    return ", ".join(f"{s['short']} {s['above']}/{s['n']}" for s in secs)


def fundamentals_lines(f: dict | None) -> list[str]:
    if not f:
        return ["Fundamentals: not available yet"]
    lines = [f"Fundamentals: *{f['passed']}/{f['applicable']} passed* — {f['verdict']}"]
    for c in f["checks"]:
        lines.append(f"{STATUS_EMOJI[c['status']]} {c['label']}: {c['display']}")
    return lines


def stock_line(st: dict) -> str:
    bits = [
        _fmt_price(st["close"]),
        f"{st['vs50']:+.1f}% vs 50 DMA" if st["vs50"] is not None else None,
        f"RS {st['rs_3m']:+.1f}% vs Nifty (3M)" if st["rs_3m"] is not None else None,
        f"{abs(st['off_high']):.1f}% off 52W high" if st["off_high"] is not None else None,
    ]
    tags = []
    if st.get("fresh_cross"):
        tags.append("fresh 50 DMA breakout")
    if st.get("extended"):
        tags.append("extended — wait for pullback")
    out = " | ".join(b for b in bits if b)
    if tags:
        out += f"\n_{'; '.join(tags)}_"
    return out


def compose(events: list[dict], res: dict, fundamentals: dict, cfg: dict,
            dashboard_url: str = "", new_session: bool = True) -> list[dict]:
    """Turn events into WhatsApp messages: [{type, title, text}]."""
    m = res["market"]
    regimes = res["regimes"]
    stocks = {s["symbol"]: s for s in res["stocks"]}
    msgs = []

    def add(t, title, lines):
        msgs.append({"type": t, "title": title, "text": "\n".join(lines)})

    for ev in events:
        t = ev["type"]
        if t == "welcome":
            add(t, "Analyser started", [
                _header(res), "✅ Analyser is live. You'll get alerts here.", "",
                f"{TONE_EMOJI[m['regime_tone']]} Market mode: *{m['regime_label']}*",
                *_breadth_lines(res), "",
                f"Strong sectors: {_top_sectors(res)}",
                f"Watchlist ({len(res['watchlist'])}): {', '.join(res['watchlist'][:12]) or 'none yet'}",
            ])
        elif t == "mode_change":
            old = regimes.get(ev["old"], {}).get("label", ev["old"])
            add(t, f"Mode: {old} → {m['regime_label']}", [
                _header(res),
                f"{TONE_EMOJI[m['regime_tone']]} Market mode changed: *{m['regime_label']}*",
                f"(was {old})", "",
                *_breadth_lines(res), "",
                m["advice"], "",
                f"Top sectors: {_top_sectors(res)}",
            ])
        elif t == "thrust":
            add(t, "Breadth thrust", [
                _header(res),
                "🚀 *BREADTH THRUST* — Nifty 50 breadth jumped from oversold to "
                f"{m['b50']:.0f}% within {cfg['breadth']['thrust_window']} sessions.",
                "Rare signal: broad buying across the index. Review the watchlist for swing entries.",
            ])
        elif t == "new_stock":
            st = stocks.get(ev["symbol"])
            if not st:
                continue
            add(t, f"New in watchlist: {st['symbol']}", [
                _header(res),
                f"⭐ *New stock in watchlist: {st['symbol']}*",
                f"{st['name']} · {st['sector_short']}",
                stock_line(st), "",
                *fundamentals_lines(fundamentals.get(st["symbol"])), "",
                f"Market mode: {m['regime_label']}",
            ])
        elif t == "exits":
            names = ", ".join(ev["symbols"])
            add(t, f"Dropped: {names}", [
                _header(res), f"↘️ Dropped from watchlist: *{names}*",
                "(no longer meets the 50 DMA / strength / sector-breadth rules)",
            ])
        elif t == "index_change":
            parts = [_header(res), "📋 *Nifty 50 constituents changed*"]
            if ev["added"]:
                parts.append(f"Added: {', '.join(ev['added'])}")
            if ev["removed"]:
                parts.append(f"Removed: {', '.join(ev['removed'])}")
            add(t, "Nifty 50 constituents changed", parts)

    if cfg["alerts"].get("daily_summary", True) and new_session and not any(e["type"] == "welcome" for e in events):
        lines = [
            f"*VESTORA ANALYST — {_date(res['asof'])} close*",
            f"{TONE_EMOJI[m['regime_tone']]} Mode: *{m['regime_label']}* (score {m['score']:.0f})",
            *_breadth_lines(res)[:2],
            f"Strong sectors: {_top_sectors(res)}",
            f"Watchlist ({len(res['watchlist'])}): {', '.join(res['watchlist'][:12]) or 'none'}",
        ]
        if dashboard_url:
            lines.append(f"Dashboard: {dashboard_url}")
        add("summary", f"Close: {m['regime_label']} · {m['b50']:.0f}% above 50 DMA", lines)
    elif dashboard_url and msgs:
        msgs[-1]["text"] += f"\n\nDashboard: {dashboard_url}"

    limit = int(cfg["alerts"].get("max_message_chars", 1400))
    for x in msgs:
        if len(x["text"]) > limit:
            x["text"] = x["text"][: limit - 3] + "..."
    return msgs


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
def send_callmebot(phone: str, apikey: str, text: str) -> tuple[bool, str]:
    import requests
    try:
        r = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": phone, "text": text, "apikey": apikey},
            timeout=30,
        )
        body = r.text[:300]
        ok = r.status_code == 200 and "error" not in body.lower()
        return ok, f"HTTP {r.status_code}: {body}"
    except Exception as exc:
        return False, str(exc)


def send_textmebot(phone: str, apikey: str, text: str) -> tuple[bool, str]:
    """TextMeBot: sends from the WhatsApp account linked to your TextMeBot key."""
    import requests
    try:
        r = requests.get(
            "https://api.textmebot.com/send.php",
            params={"recipient": phone, "apikey": apikey, "text": text},
            timeout=40,
        )
        body = r.text[:300]
        low = body.lower()
        ok = r.status_code == 200 and not any(w in low for w in ("error", "invalid", "not connected", "disconnected"))
        return ok, f"HTTP {r.status_code}: {body}"
    except Exception as exc:
        return False, str(exc)


def send_twilio(phone: str, text: str) -> tuple[bool, str]:
    import requests
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    sender = os.environ.get("TWILIO_WHATSAPP_FROM", "")
    if not (sid and token and sender):
        return False, "Twilio secrets missing"
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data={"From": f"whatsapp:{sender}", "To": f"whatsapp:{phone}", "Body": text},
            auth=(sid, token), timeout=30,
        )
        return r.status_code in (200, 201), f"HTTP {r.status_code}"
    except Exception as exc:
        return False, str(exc)


def deliver(messages: list[str], cfg: dict, dry_run: bool = False) -> list[dict]:
    """Send messages over WhatsApp. Returns a log entry per message."""
    phone = os.environ.get("WHATSAPP_PHONE", "").strip()
    provider = cfg["alerts"].get("provider", "textmebot").lower()
    results = []
    for i, text in enumerate(messages):
        if dry_run or not phone:
            reason = "dry run" if dry_run else "WHATSAPP_PHONE not set"
            log.info("[WhatsApp %s] would send:\n%s\n", reason, text)
            results.append({"ok": False, "detail": reason})
            continue
        if provider == "twilio":
            ok, detail = send_twilio(phone, text)
        elif provider == "textmebot":
            key = os.environ.get("TEXTMEBOT_APIKEY", "").strip()
            if not key:
                ok, detail = False, "TEXTMEBOT_APIKEY not set"
            else:
                ok, detail = send_textmebot(phone, key, text)
        else:
            key = os.environ.get("CALLMEBOT_APIKEY", "").strip()
            if not key:
                ok, detail = False, "CALLMEBOT_APIKEY not set"
            else:
                ok, detail = send_callmebot(phone, key, text)
        log.info("WhatsApp message %d/%d: %s (%s)", i + 1, len(messages), "sent" if ok else "FAILED", detail)
        results.append({"ok": ok, "detail": detail})
        time.sleep(7)  # leave a gap between messages so WhatsApp doesn't treat them as spam
    return results
