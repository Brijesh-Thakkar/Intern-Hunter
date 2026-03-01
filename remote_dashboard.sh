#!/usr/bin/env bash
# =============================================================================
#  remote_dashboard.sh — Run the Intern Hunter dashboard on the Oracle VM
#  and stream the output back to your terminal.
#  Usage: bash remote_dashboard.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env.deploy"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌  .env.deploy not found.  Run cloud_deploy.sh first."
    exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

if [[ -z "${ORACLE_VM_IP:-}" || -z "${ORACLE_SSH_KEY_PATH:-}" ]]; then
    echo "❌  ORACLE_VM_IP or ORACLE_SSH_KEY_PATH missing in .env.deploy"
    exit 1
fi

echo -e "\033[1m\033[36m📊  Opening Intern Hunter dashboard on Oracle VM ($ORACLE_VM_IP)…\033[0m"
echo ""

ssh \
    -i "${ORACLE_SSH_KEY_PATH}" \
    -o StrictHostKeyChecking=no \
    -o ConnectTimeout=30 \
    -t "ubuntu@${ORACLE_VM_IP}" \
    '
    cd ~/intern_hunter
    source venv/bin/activate
    echo "──────────────────────────────────────────────────"
    echo "  Service status"
    echo "──────────────────────────────────────────────────"
    systemctl is-active intern-hunter.service 2>/dev/null \
        && echo "  ✅  intern-hunter.service is RUNNING" \
        || echo "  ⚠️   intern-hunter.service is NOT running"
    echo ""
    echo "──────────────────────────────────────────────────"
    echo "  Running dashboard.py (Ctrl+C to exit)"
    echo "──────────────────────────────────────────────────"
    echo ""
    python dashboard.py
    '
