#!/usr/bin/env bash
# =============================================================================
#  update_agent.sh — Push local code changes to Oracle VM and restart agent
#  Usage: bash update_agent.sh
#  Safe to run any time — excludes data/, sessions/, credentials/, and venv/
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env.deploy"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}✅  $*${RESET}"; }
warn() { echo -e "${YELLOW}⚠️   $*${RESET}"; }
err()  { echo -e "${RED}❌  $*${RESET}"; exit 1; }
info() { echo -e "${CYAN}ℹ️   $*${RESET}"; }

if [[ ! -f "$ENV_FILE" ]]; then
    err ".env.deploy not found.  Run cloud_deploy.sh first."
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

if [[ -z "${ORACLE_VM_IP:-}" || -z "${ORACLE_SSH_KEY_PATH:-}" ]]; then
    err "ORACLE_VM_IP or ORACLE_SSH_KEY_PATH missing in .env.deploy"
fi

REMOTE="ubuntu@${ORACLE_VM_IP}"
SSH_OPTS="-i ${ORACLE_SSH_KEY_PATH} -o StrictHostKeyChecking=no -o ConnectTimeout=30"

echo ""
echo -e "${BOLD}${CYAN}  🔄  Intern Hunter — Update & Restart${RESET}"
echo ""

# ── Sync files (exclude secrets + big data dirs) ───────────────────────────
info "Syncing code to ${ORACLE_VM_IP}…"
rsync -avz --progress \
    -e "ssh ${SSH_OPTS}" \
    --exclude='.env*' \
    --exclude='.git/' \
    --exclude='data/' \
    --exclude='sessions/' \
    --exclude='credentials/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.venv/' \
    --exclude='venv/' \
    --exclude='config.yaml' \
    "${SCRIPT_DIR}/" \
    "${REMOTE}:~/intern_hunter/"

ok "Code synced"

# ── Re-install requirements (in case they changed) ──────────────────────────
info "Checking if requirements.txt changed…"
# shellcheck disable=SC2029
ssh $SSH_OPTS "$REMOTE" bash -s << 'REMOTE_UPDATE'
set -e
cd ~/intern_hunter
source venv/bin/activate
pip install -r requirements.txt -q
echo "Requirements OK"
REMOTE_UPDATE

# ── Restart the agent ────────────────────────────────────────────────────────
info "Restarting intern-hunter.service…"
ssh $SSH_OPTS "$REMOTE" "sudo systemctl restart intern-hunter.service"
sleep 3
STATUS=$(ssh $SSH_OPTS "$REMOTE" "systemctl is-active intern-hunter.service" 2>/dev/null || echo "unknown")

if [[ "$STATUS" == "active" ]]; then
    ok "intern-hunter.service restarted and running"
else
    warn "Service status: $STATUS — last 20 log lines:"
    ssh $SSH_OPTS "$REMOTE" "sudo journalctl -u intern-hunter -n 20 --no-pager" || true
fi

echo ""
echo -e "${BOLD}  Done! Run ${CYAN}bash remote_logs.sh${RESET}${BOLD} to watch live logs.${RESET}"
echo ""
