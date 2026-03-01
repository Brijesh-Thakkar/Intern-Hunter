# Oracle Cloud Free Tier — Intern Hunter Deployment Guide

Get your agent running 24/7 on Oracle's **always-free** ARM VM in under 30 minutes.

---

## What You Get (Free Forever)

| Resource | Free Tier Value |
|---|---|
| Shape | VM.Standard.A1.Flex (ARM Ampere) |
| OCPUs | 4 |
| RAM | 24 GB |
| Storage | 200 GB block volume |
| Bandwidth | 10 TB/month outbound |
| Cost | **$0/month, forever** |

Oracle's Free Tier never auto-charges. Unlike AWS/GCP, you won't wake up to a surprise bill.

---

## Part 1 — Create Oracle Cloud Account

1. Go to **https://cloud.oracle.com** and click **Start for free**

2. Fill in your details:
   - Use a **real phone number** (SMS verification required)
   - Use a **credit/debit card** (for identity verification only — not charged)
   - Choose **Home Region** carefully: Select the region closest to you  
     (India → `ap-mumbai-1` or `ap-hyderabad-1`)  
     ⚠️ **Home region cannot be changed after signup**

3. Complete email and phone verification

4. Wait for the "Your account is fully provisioned" email (usually 5–15 min)

---

## Part 2 — Create the VM

### 2.1 — Generate SSH Key Pair (on your laptop)

```bash
ssh-keygen -t rsa -b 4096 -f ~/.ssh/oracle_intern_hunter -N ""
# Public key → ~/.ssh/oracle_intern_hunter.pub  (share with Oracle)
# Private key → ~/.ssh/oracle_intern_hunter     (keep secret, never share)
```

### 2.2 — Create the Instance

1. Sign in to **https://cloud.oracle.com**

2. Top-left menu → **Compute** → **Instances** → **Create instance**

3. Fill in the form:

   **Name:** `intern-hunter`

   **Image and shape:**
   - Click **Edit**
   - Image: **Ubuntu 22.04** (Canonical)
   - Shape: Click **Change shape**
     - Instance type: **Ampere** (ARM)
     - Shape: **VM.Standard.A1.Flex**
     - OCPUs: `2` (or 4 – both free)
     - Memory: `12 GB` (or 24 – both free)
   - Click **Select shape**

   **Networking:**
   - Leave defaults (new VCN created automatically)
   - ✅ **Assign a public IPv4 address** — must be checked

   **Add SSH keys:**
   - Select **Upload public key file (.pub)**
   - Upload `~/.ssh/oracle_intern_hunter.pub`

4. Click **Create** — VM will be RUNNING in ~2 minutes

5. From the instance detail page, copy the **Public IP address**

---

## Part 3 — Open Port 22 in Firewall

Oracle blocks all ports by default. You need to allow SSH.

1. On the instance detail page → **Primary VNIC** → click the subnet name

2. Click the **Default Security List**

3. **Add Ingress Rule:**
   - Source Type: CIDR
   - Source CIDR: `0.0.0.0/0`
   - IP Protocol: TCP
   - Destination Port Range: `22`
   - Click **Add Ingress Rules**

> 💡 **Also open port 80/443** if you ever want to add a web dashboard later.

---

## Part 4 — Test SSH Connection

```bash
ssh -i ~/.ssh/oracle_intern_hunter ubuntu@YOUR_VM_IP
```

You should see the Ubuntu welcome banner. Type `exit` to leave.

If you get "Permission denied":
- Make sure you're using the right key: `-i ~/.ssh/oracle_intern_hunter`
- Check the key file permissions: `chmod 600 ~/.ssh/oracle_intern_hunter`

---

## Part 5 — Deploy Intern Hunter

From your laptop, inside the `intern_hunter/` folder:

```bash
bash cloud_deploy.sh
```

The script will:
1. Ask for your 10 credentials (Mistral key, Gmail, Telegram, Internshala, VM IP)
2. Package the project (excluding data/ and sessions/)
3. Upload via SCP to the VM
4. Install all deps + Playwright Chromium (takes ~3 min)
5. Write `config.yaml` with your credentials on the VM
6. Create and start a systemd service that auto-restarts if it crashes
7. Run a quick dry-run test and show results

---

## Part 6 — Set Up Auto-Apply Sessions

The agent needs browser cookies to apply to jobs on your behalf.

### Internshala Sessions

**Option A — On your laptop (recommended):**
```bash
cd intern_hunter
python setup.py --internshala-only
# This opens a browser window. Log in to Internshala manually.
# Then the script saves cookies and SCPs them to the VM automatically.
```

**Option B — Generate locally and upload manually:**
```bash
python setup.py --internshala-only
# Saves to intern_hunter/sessions/internshala.json
# Then upload:
scp -i ~/.ssh/oracle_intern_hunter \
    sessions/internshala.json \
    ubuntu@YOUR_VM_IP:~/intern_hunter/sessions/
```

### LinkedIn Sessions (same process)
```bash
# Modify setup.py step to save linkedin session
# Then upload:
scp -i ~/.ssh/oracle_intern_hunter \
    sessions/linkedin.json \
    ubuntu@YOUR_VM_IP:~/intern_hunter/sessions/
```

---

## Part 7 — Daily Management

### Watch live logs
```bash
bash remote_logs.sh
```

### View job stats dashboard
```bash
bash remote_dashboard.sh
```

### Push local code changes
```bash
bash update_agent.sh
```

### SSH directly
```bash
ssh -i ~/.ssh/oracle_intern_hunter ubuntu@YOUR_VM_IP
```

### Common commands on the VM
```bash
# Check if agent is running
sudo systemctl status intern-hunter

# Restart the agent
sudo systemctl restart intern-hunter

# View last 100 log lines
sudo journalctl -u intern-hunter -n 100 --no-pager

# Run a manual scan right now
cd ~/intern_hunter
source venv/bin/activate
python agent.py --dry-run

# View jobs in database
python dashboard.py

# Reset seen jobs (force re-evaluation of all jobs)
python agent.py --reset --dry-run
```

---

## Troubleshooting

### Agent keeps restarting
```bash
sudo journalctl -u intern-hunter -n 50 --no-pager
```
Common causes:
- Bad config.yaml (check YAML syntax)
- Mistral API key wrong or quota exceeded
- Playwright can't launch (check xvfb is running: `systemctl status xvfb`)

### Playwright browser crashes
```bash
sudo systemctl status xvfb
sudo systemctl restart xvfb
sudo systemctl restart intern-hunter
```

### No jobs found
- Sessions may have expired — re-run session capture and re-upload
- Internshala/LinkedIn may have changed their HTML — check logs for selector errors

### VM runs out of memory
The A1.Flex with 12GB RAM handles Playwright + Python easily.
If issues arise, reduce `max_applications_per_day` in the agent config.

### Update credentials
Re-run `cloud_deploy.sh` — it detects existing `.env.deploy` and shows
current values in brackets. Just press Enter to keep them.

---

## Architecture Overview

```
Your Laptop                           Oracle Cloud VM (free forever)
───────────                           ──────────────────────────────
cloud_deploy.sh  ──── SCP ────────►  ~/intern_hunter/
                 ──── SSH config ──►  config.yaml (with credentials)
                                      │
remote_logs.sh   ──── SSH ────────►  journalctl -u intern-hunter -f
remote_dashboard.sh ─ SSH ────────►  python dashboard.py
update_agent.sh  ──── rsync+SSH ──►  code sync + systemctl restart
                                      │
                                      systemd service
                                      ├─ intern-hunter.service (Restart=always)
                                      ├─ xvfb.service (virtual display)
                                      └─ cron watchdog (every 10 min)
                                         │
                                         python agent.py --schedule
                                         ├─ Scrape Internshala (every 6h)
                                         ├─ Scrape LinkedIn (every 6h)
                                         ├─ Score with Mistral AI
                                         ├─ Alert HIGH PRIORITY via email/Telegram
                                         └─ Daily digest at 8AM
```

---

## Security Notes

- `.env.deploy` is chmod 600 and in `.gitignore` — never committed
- `config.yaml` on the VM is also in `.gitignore` — written fresh each deploy
- SSH key should never be shared or committed
- Use Gmail App Password (not your real Gmail password)
- Telegram bot token is optional but recommended for instant alerts

---

*Intern Hunter — built for IIT Jodhpur CS/EE students, made to run forever*
