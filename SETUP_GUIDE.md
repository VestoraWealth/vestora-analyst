# VESTORA ANALYST — setup guide

About 20 minutes, no coding. You need the company phone (WhatsApp 9048595965), your phone (9048595992) and a computer with a browser.

When it's done:

- Every trading day at about **4:15 PM IST** the analyser downloads Nifty 50 prices, works out market breadth, picks the strongest sectors and stocks, and checks the fundamentals of any new pick.
- You get a **WhatsApp message** when the market mode changes, when a stock enters or leaves the watchlist (with its fundamental check), when the Nifty 50 adds or drops a company, and a short end-of-day summary.
- The **dashboard** is updated at `https://<your-github-name>.github.io/vestora-analyst/`.

It runs on GitHub's free servers, so your computer doesn't need to be on.

---

## Step 1 — Set up WhatsApp sending (TextMeBot, about 10 minutes)

Alerts are sent **from** the Vestora company WhatsApp (9048595965) **to** your number (9048595992), through **TextMeBot** (textmebot.com, $6/month "unlimited" plan or $60/year; 2-day free trial).

1. On textmebot.com, request an **ApiKey**. It arrives by email.
2. Open the email and follow its links: add your phone number, then **link WhatsApp**. A QR code appears.
3. On the company phone (9048595965): WhatsApp → **Settings** (or the ⋮ menu) → **Linked devices** → **Link a device** → scan the QR code.
4. Choose the **$6/month unlimited** plan (the $1 plan only messages the linked number itself, and here the receiver is a different number).
5. Keep the **ApiKey** private. You'll paste it into GitHub in Step 4.

Open WhatsApp on the company phone at least once every two weeks. WhatsApp logs out linked devices when the main phone has been inactive for 14 days.

## Step 2 — Create a free GitHub account

Go to **github.com/signup** and create an account. Note your username: it becomes part of your dashboard address.

## Step 3 — Put the analyser on GitHub

1. Click **+** (top right) → **New repository**.
2. Repository name: `vestora-analyst` · choose **Public** · click **Create repository**.
3. On the next page click **uploading an existing file**.
4. Unzip `vestora-analyst.zip` on your computer. Open the folder and drag **everything inside it** into the upload box. Click **Commit changes**.
5. The upload box skips hidden folders, so add the schedule file by hand:
   - Click **Add file → Create new file**.
   - In the name box type exactly: `.github/workflows/vestora-analyst.yml`
   - Open `workflow-file.yml` from the unzipped folder (any text editor), copy all of it, and paste it into the big box.
   - Click **Commit changes**.

> Shortcut: connect GitHub to Claude (claude.ai → Settings → Connectors → GitHub), create the empty `vestora-analyst` repository, and ask Claude to push the files for you. You'd then only do steps 4 and 5 below.

## Step 4 — Add your phone number and key (kept secret)

In your repository: **Settings → Secrets and variables → Actions → New repository secret**. Add two secrets:

| Name | Value |
|---|---|
| `WHATSAPP_PHONE` | `+919048595992` (the number that receives alerts) |
| `TEXTMEBOT_APIKEY` | the ApiKey TextMeBot emailed you |

Secrets are hidden from everyone, including on a public repository.

## Step 4b — Email alerts (recommended, works alongside WhatsApp)

Every run also sends **one email** with the same alerts.

1. Your Google account needs **2-Step Verification** turned on (myaccount.google.com → Security).
2. Open **myaccount.google.com/apppasswords**, type the name `Vestora Analyst`, and tap **Create**. Google shows a 16-letter password. Copy it.
3. In GitHub, add two more secrets:

| Name | Value |
|---|---|
| `EMAIL_ADDRESS` | your Gmail address |
| `EMAIL_APP_PASSWORD` | the 16-letter app password |

Optional: add `EMAIL_TO` if the alerts should go to a different address.

## Step 5 — Switch on the dashboard

**Settings → Pages → Build and deployment → Source: GitHub Actions.**

## Step 6 — First run

1. Open the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
2. Click **Vestora Analyst** on the left → **Run workflow** → mode **test-alerts** → **Run workflow**.
   Within a minute 9048595992 should get "Test message — WhatsApp alerts are working." from the company number.
3. Run it again with mode **normal**. You'll get the welcome message with today's market mode and watchlist, and the dashboard goes live at
   `https://<your-github-name>.github.io/vestora-analyst/` (the link also appears in the run's summary).

From now on it runs by itself every weekday after market close.

---

Need today's update again? Run the workflow with mode **send-now**.

## Changing the rules

All thresholds are in **`config.yaml`**. Open it on GitHub, click the pencil icon, change a number, and click **Commit changes**. The next run uses the new value. For example:

- `best_zone_min: 60` — breadth needed for "BEST TIME FOR SWINGS"
- `min_breadth_pct: 50` — breadth a sector needs for its stocks to make the watchlist
- `min_profit_cagr_3y_pct: 15`, `min_piotroski: 6`, `max_debt_to_equity: 0.5` — fundamental filters
- `daily_summary: false` — turn off the daily summary and keep only event alerts

## What each WhatsApp alert means

| Alert | When it's sent |
|---|---|
| Market mode changed | The swing-timing mode changes and holds for two sessions (e.g. NEUTRAL → BEST TIME FOR SWINGS) |
| Breadth thrust | Breadth jumps from oversold (≤25%) to 60%+ within 10 sessions. Rare and strong |
| New stock in watchlist | A stock passes the leader rules in a sector with good breadth. Includes the 7-point fundamental check |
| Dropped from watchlist | A listed stock closes below its 50 DMA or its sector's breadth collapses |
| Nifty 50 constituents changed | NSE adds or removes a company from the index |
| Daily summary | Every trading day at close |

## About the data

- **Prices:** Yahoo Finance daily closes (adjusted), 2 years of history.
- **Fundamentals:** Yahoo Finance annual reports (up to 4 years). Piotroski F-score is calculated from those reports; for banks and NBFCs, tests that don't apply (gross margin, current ratio) are left out and the score is shown out of the tests available.
- **Promoter pledge:** NSE pledge disclosures. If NSE blocks the request, the check shows **"verify on NSE"** instead of guessing. You can type a known value under `pledge_overrides` in `config.yaml`.
- **Debt / equity** isn't meaningful for lenders and insurers, so it shows N/A for Financial Services.

To cross-check fundamentals on Screener.in (free login), create a screen with this query:

```
Debt to equity < 0.5 AND
Sales growth 3Years > 15 AND
Pledged percentage = 0 AND
Net profit > 0 AND
Profit growth 3Years > 15 AND
Piotroski score > 5 AND
Market Capitalization > 10000
```

## If something goes wrong

- **No WhatsApp message:** Actions tab → open the latest run → "Run VESTORA ANALYST" step. A line says `WhatsApp message 1/1: sent` or `FAILED` with the reason. Check the two secrets are spelled exactly as above, and that "Linked devices" on the company phone still shows TextMeBot.
- **Red ❌ on a run:** usually Yahoo Finance was busy. The next day's run normally works; you can also click **Re-run jobs**.
- **GitHub email saying the schedule was disabled:** GitHub pauses schedules on repositories with no activity for 60 days. Open Actions → Vestora Analyst → **Enable workflow**.
- **Want alerts from another provider:** in `config.yaml` set `provider: callmebot` (free, add secret `CALLMEBOT_APIKEY`) or `provider: twilio` (add `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`).

## Optional — use your own domain

To show the dashboard at `analyst.vestorawealth.com`:

1. GoDaddy → your domain → **DNS** → **Add record**: Type **CNAME**, Name `analyst`, Value `<your-github-name>.github.io`.
2. GitHub → Settings → Pages → **Custom domain**: `analyst.vestorawealth.com` → Save → tick **Enforce HTTPS** once it's available.
3. Settings → Secrets and variables → Actions → **Variables** tab → New variable `DASHBOARD_URL` = `https://analyst.vestorawealth.com/` so WhatsApp messages link to it.

The dashboard shows only public market data. Anyone with the link can open it.
