"""Writes the dashboard (docs/index.html) and its data file (docs/data.json)."""
from __future__ import annotations

import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template" / "dashboard.html"
LOGO = ROOT / "template" / "vestora_mark.png"


HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<style>[hidden]{display:none!important}body{margin:0}img{max-width:100%}</style></head><body>')


def render(data: dict, out_dir: Path, fragment_path: Path | None = None) -> Path:
    """Write docs/index.html (full page for GitHub Pages) and docs/data.json.

    fragment_path, if given, also receives the page body alone (used for Claude artifacts).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    html = TEMPLATE.read_text(encoding="utf-8")
    logo = "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode()
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("__LOGO_DATA_URI__", logo)
    html = html.replace('"__VESTORA_DATA__"', payload)
    (out_dir / "data.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    out = out_dir / "index.html"
    out.write_text(HEAD + html + "</body></html>", encoding="utf-8")
    if fragment_path:
        Path(fragment_path).write_text(html, encoding="utf-8")
    return out
