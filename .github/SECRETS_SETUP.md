# GitHub Secrets Setup — Intern Hunter

## Where to add secrets

**GitHub repo → Settings → Secrets and variables → Actions → New repository secret**

Direct link: `https://github.com/Brijesh-Thakkar/My_Agent/settings/secrets/actions`

---

## Required Secrets

| Secret Name | Where to get it | Required? |
|---|---|---|
| `MISTRAL_API_KEY` | [console.mistral.ai](https://console.mistral.ai) → API Keys | ✅ Yes |
| `GMAIL_SENDER` | Your Gmail address (e.g. `you@gmail.com`) | ✅ Yes |
| `GMAIL_APP_PASSWORD` | [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) — 16 chars, no spaces | ✅ Yes |
| `COLLEGE_EMAIL` | Your IIT Jodhpur email (for notifications) | ✅ Yes |
| `INTERNSHALA_SESSION` | Run `bash save_sessions_to_secrets.sh` locally | ✅ Yes (for auto-apply) |
| `LINKEDIN_SESSION` | Run `bash save_sessions_to_secrets.sh` locally | ⚡ Optional |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` | ⚡ Optional |
| `TELEGRAM_CHAT_ID` | Send any message to your bot → `https://api.telegram.org/bot{TOKEN}/getUpdates` → find `chat.id` | ⚡ Optional |

---

## How to get Gmail App Password (step by step)

1. Go to [myaccount.google.com](https://myaccount.google.com) → **Security**
2. Enable **2-Step Verification** if not already on
3. Search for **"App Passwords"** in the search bar
4. Select App: **Mail** | Device: **Other** (type "Intern Hunter")
5. Click **Generate** — copy the 16-character password
6. **Remove the spaces** before pasting into the GitHub Secret

---

## How to encode session cookies

Run this command **locally** (after running `python setup.py` at least once):

```bash
bash save_sessions_to_secrets.sh
```

This will print base64-encoded versions of your Playwright session cookies.  
Copy the output and paste it as the secret value.

### Why base64?
GitHub Secrets can't store raw JSON with special characters reliably.  
Base64 encodes the full JSON file as a safe ASCII string.

### How it works in GitHub Actions
1. `inject_config.py` reads the base64 secret from env var
2. Decodes it back to JSON
3. Writes `sessions/internshala.json` (and `sessions/linkedin.json`) to disk
4. Playwright picks up the cookies → you're "logged in" during the run

---

## Refreshing sessions (when cookies expire)

Internshala sessions typically last **30–90 days**. LinkedIn sessions last longer.

When you see "session expired" errors in the Actions logs:

```bash
# Log in again locally
python setup.py

# Re-encode and update the GitHub Secret
bash save_sessions_to_secrets.sh
```

Then copy the new value into the GitHub Secret (overwrite the old one).

---

## Schedule

The workflow runs automatically at **00:00, 06:00, 12:00, 18:00 UTC** every day.

- 00:00 UTC = 05:30 IST
- 06:00 UTC = 11:30 IST
- 12:00 UTC = 17:30 IST
- 18:00 UTC = 23:30 IST

**Free tier limit:** GitHub Actions gives 2,000 minutes/month on free accounts.  
Each run takes ~10–20 minutes → 4 runs/day × 30 days = ~120 runs × 15 min = **~1,800 min/month** (just within the limit).

---

## Manual run (test without waiting for schedule)

1. Go to **GitHub → Actions → 🎯 Intern Hunter — Auto Apply**
2. Click **Run workflow**
3. Check **"Dry run"** to test without submitting real applications
4. Click **Run workflow** — watch the logs in real time

---

## Viewing results

- **Logs:** GitHub → Actions → click the latest run → expand each step
- **Screenshots:** Each run uploads a `screenshots-{run_number}` artifact (kept 3 days)
- **Dashboard:** The "Show Dashboard Summary" step prints stats at the end of every run

---

## Troubleshooting

| Error | Fix |
|---|---|
| `Required secrets missing` | Add MISTRAL_API_KEY, GMAIL_SENDER, GMAIL_APP_PASSWORD to repo secrets |
| `Session expired` or login redirect | Re-run `save_sessions_to_secrets.sh` and update secrets |
| `playwright: command not found` | Check `requirements.txt` includes `playwright>=1.49.0` |
| `No module named yaml` | Check `requirements.txt` includes `pyyaml>=6.0.1` |
| Workflow not triggering on schedule | GitHub disables scheduled workflows after 60 days of repo inactivity — push any commit to re-enable |
