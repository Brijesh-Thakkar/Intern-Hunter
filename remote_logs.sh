#!/usr/bin/env bash
# =============================================================================
#  remote_logs.sh — Tail live logs from the Oracle Cloud VM's intern-hunter
#  Usage: bash remote_logs.sh
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

echo -e "\033[1m\033[36m📡  Streaming logs from Oracle VM ($ORACLE_VM_IP) — Ctrl+C to stop\033[0m"
echo ""

ssh \
    -i "${ORACLE_SSH_KEY_PATH}" \
    -o StrictHostKeyChecking=no \
    -o ConnectTimeout=30 \
    -t "ubuntu@${ORACLE_VM_IP}" \
    "sudo journalctl -u intern-hunter -f --output=cat"
