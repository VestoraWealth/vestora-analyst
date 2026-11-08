"""Alert detection, WhatsApp message text and delivery (CallMeBot or Twilio)."""
from __future__ import annotations

import logging
import re
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


def _wa_to_html(text: str) -> str:
    import html as _h
    t = _h.escape(text)
    t = re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", t)
    t = re.sub(r"(^|\s)_([^_\n]+)_", r"\1<i>\2</i>", t)
    t = re.sub(r"(https?://[^\s<]+)", r'<a href="\1" style="color:#B8860B">\1</a>', t)
    return t.replace("\n", "<br>")


def send_email(subject: str, parts: list[str]) -> tuple[bool, str]:
    """One email per run, through Gmail (or any SMTP server) with an app password."""
    import smtplib
    import ssl
    from email.message import EmailMessage
    user = os.environ.get("EMAIL_ADDRESS", "").strip()
    pw = os.environ.get("EMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    to = os.environ.get("EMAIL_TO", "").strip() or user
    if not (user and pw):
        return False, "EMAIL_ADDRESS / EMAIL_APP_PASSWORD not set"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"VESTORA ANALYST <{user}>"
    msg["To"] = to
    plain = "\n\n----------------------------------------\n\n".join(re.sub(r"\*([^*\n]+)\*", r"\1", p) for p in parts)
    msg.set_content(plain)
    blocks = "".join(
        f'<div style="padding:16px 0;border-top:1px solid #E6DCC8;font-size:15px;line-height:1.55">{_wa_to_html(p)}</div>'
        for p in parts)
    msg.add_alternative(
        '<div style="background:#F8F3E9;padding:20px;font-family:Arial,Helvetica,sans-serif;color:#2A1A0B">'
        '<div style="max-width:640px;margin:0 auto;background:#FFFDF8;border:1px solid #E6DCC8;border-radius:12px;overflow:hidden">'
        '<div style="background:#2A1A0B;color:#D4AF37;padding:16px 20px;font-weight:bold;letter-spacing:3px;font-size:18px">'
        'VESTORA ANALYST</div>'
        f'<div style="padding:4px 20px 16px">{blocks}</div>'
        '<div style="padding:12px 20px;font-size:12px;color:#857055;border-top:1px solid #E6DCC8">'
        'For research and education only. Not investment advice.</div></div></div>',
        subtype="html")
    host = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com").strip() or "smtp.gmail.com"
    port = int(os.environ.get("EMAIL_SMTP_PORT", "465") or 465)
    try:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(user, pw)
            smtp.send_message(msg)
        return True, f"emailed {to}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _send_whatsapp(phone: str, text: str, cfg: dict) -> tuple[bool, str]:
    provider = cfg["alerts"].get("provider", "textmebot").lower()
    if provider == "twilio":
        return send_twilio(phone, text)
    if provider == "textmebot":
        key = os.environ.get("TEXTMEBOT_APIKEY", "").strip()
        return send_textmebot(phone, key, text) if key else (False, "TEXTMEBOT_APIKEY not set")
    key = os.environ.get("CALLMEBOT_APIKEY", "").strip()
    return send_callmebot(phone, key, text) if key else (False, "CALLMEBOT_APIKEY not set")


def deliver(messages: list[str], cfg: dict, dry_run: bool = False,
            subject: str = "VESTORA ANALYST") -> list[dict]:
    """Send messages by WhatsApp (one message each) and email (one email for the batch).

    A message counts as delivered when at least one channel succeeded.
    """
    ac = cfg["alerts"]
    results = [{"ok": False, "detail": ""} for _ in messages]
    if not messages:
        return results
    if dry_run:
        for text in messages:
            log.info("[dry run] would send:\n%s\n", text)
        return [{"ok": False, "detail": "dry run"} for _ in messages]

    if ac.get("whatsapp", True):
        phone = os.environ.get("WHATSAPP_PHONE", "").strip()
        if not phone:
            log.info("WhatsApp skipped: WHATSAPP_PHONE not set")
        else:
            for i, text in enumerate(messages):
                ok, detail = _send_whatsapp(phone, text, cfg)
                log.info("WhatsApp message %d/%d: %s (%s)", i + 1, len(messages), "sent" if ok else "FAILED", detail)
                results[i]["ok"] |= ok
                results[i]["detail"] += f"whatsapp: {'sent' if ok else 'failed'}; "
                if i < len(messages) - 1:
                    time.sleep(7)  # gap between messages so WhatsApp doesn't treat them as spam

    if ac.get("email", True):
        ok, detail = send_email(subject, messages)
        log.info("Email: %s (%s)", "sent" if ok else "FAILED", detail)
        for r in results:
            r["ok"] |= ok
            r["detail"] += f"email: {'sent' if ok else 'failed'}; "
    return results
