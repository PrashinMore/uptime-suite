# Uptime Robot

Lightweight uptime checker that runs on GitHub Actions. It pings every URL in `urls.json` on a configurable interval (default **60 seconds**) and sends an email when a site goes down or comes back up.

## How it works

GitHub Actions schedules cannot run more often than every 5 minutes, so this workflow starts a long-running job that loops locally:

1. A job starts on a 4-hour cron (or manually via **Actions → Uptime Robot → Run workflow**).
2. `monitor.py` checks all URLs, sleeps `intervalSeconds`, and repeats for about 3 hours 50 minutes.
3. The next scheduled run continues monitoring. Concurrency keeps only one active monitor at a time without cancelling the current one.
4. Status is stored in `state.json`. Emails are sent only on **transitions** (up→down or down→up), not every minute while a site stays down.

**Note:** This needs a near-continuous runner (~43,000 minutes/month). Use a **public** repository for unlimited GitHub-hosted minutes. A private repo will burn through the free 2,000-minute quota quickly.

## Configuration

Edit [`urls.json`](urls.json):

```json
{
  "intervalSeconds": 60,
  "timeoutSeconds": 45,
  "urls": [
    "https://bizzness-d864.onrender.com"
  ]
}
```

| Field | Meaning |
| --- | --- |
| `urls` | Sites to check (HTTP 200–399 = up) |
| `intervalSeconds` | Seconds between full check rounds (default 60) |
| `timeoutSeconds` | Per-request timeout (default 45; helpful for Render cold starts) |

## Optional email alerts

Email is optional. With no SMTP secrets set, the monitor still runs and prints alerts to the Actions log. When all six secrets below are set, alerts are emailed instead.

Settings → Secrets and variables → Actions:

| Secret | Example |
| --- | --- |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | [Gmail App Password](https://support.google.com/accounts/answer/185833) |
| `ALERT_FROM` | same as `SMTP_USER` (or your from-address) |
| `ALERT_TO` | address that should receive alerts |

### Gmail setup

1. Enable 2-Step Verification on the Google account.
2. Create an App Password and store it as `SMTP_PASSWORD`.
3. Use port `587` (STARTTLS).

## Local testing

```bash
# Single check (prints alerts if SMTP env vars are unset)
python monitor.py --once
```

Export the same `SMTP_*` / `ALERT_*` variables locally if you want a real email.

## Starting the monitor

1. Push this repo to GitHub (public recommended).
2. Optionally add the six SMTP secrets above for email alerts.
3. Open **Actions → Uptime Robot → Run workflow** once to start immediately (or wait for the 4-hour cron).
