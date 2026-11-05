# VESTORA ANALYST

Nifty 50 market-breadth analyser by Vestora Wealth. It answers one question every trading day: **is this a good time to take swing trades, and in which stocks?**

- **Market breadth:** % of Nifty 50 stocks above their 50-day and 200-day moving averages, with a one-year history chart.
- **Swing-timing mode:** BEST TIME / GOOD / NEUTRAL / FADING / OVERHEATED / AVOID / OVERSOLD / EARLY REVERSAL / BREADTH THRUST, plus a 0–100 swing conditions score.
- **Sector breadth:** each sector's share of stocks above the 50 DMA, ranked with 1-month strength vs Nifty.
- **Watchlist:** the best stocks in sectors with good breadth (above a rising 50 DMA, 50 DMA above 200 DMA, beating Nifty over 3 months, near 52-week highs).
- **Fundamental check** on every new pick: debt/equity < 0.5, 3-year sales CAGR ≥ 15%, zero promoter pledge, profitable, 3-year profit CAGR ≥ 15%, Piotroski ≥ 6, market cap > ₹10,000 Cr.
- **WhatsApp alerts** for mode changes, breadth thrusts, new or dropped stocks, Nifty 50 index changes and a daily summary.

**Setup:** see [SETUP_GUIDE.md](SETUP_GUIDE.md).

## Files

| File | What it is |
|---|---|
| `config.yaml` | Every threshold and alert switch. Edit this to tune the analyser |
| `run.py` | Runs one analysis pass |
| `vestora/market.py` | Breadth, swing-timing model, sectors, watchlist |
| `vestora/fundamentals.py` | The 7-point fundamental screen and Piotroski F-score |
| `vestora/notify.py` | Alert rules and WhatsApp messages |
| `vestora/universe.py` | Nifty 50 list (downloaded live from NSE, with a built-in fallback) |
| `template/dashboard.html` | The dashboard design |
| `docs/` | The published dashboard (written by each run) |
| `state/state.json` | The analyser's memory between runs (last mode, watchlist, alert log) |
| `.github/workflows/vestora-analyst.yml` | The daily schedule (a copy is in `workflow-file.yml`) |

## Run it on a computer (optional)

```
pip install -r requirements.txt
python run.py --demo        # simulated data, nothing sent
python run.py --dry-run     # live data, prints WhatsApp messages instead of sending
python -m pytest tests      # checks the calculations
```

For research and education. Not investment advice.
