#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  save_sessions_to_secrets.sh
#  Run this LOCALLY (once) after logging in via setup.py.
#  It base64-encodes your Playwright session cookies so you can paste them
#  into GitHub Secrets.
#
#  Usage:
#    bash save_sessions_to_secrets.sh
#
#  Then go to:
#    GitHub repo → Settings → Secrets and variables → Actions → New secret
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║        Intern Hunter — Session Cookie Encoder for GitHub        ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# ── Internshala ───────────────────────────────────────────────────────────────
echo "=== INTERNSHALA SESSION ==="
if [ -f "sessions/internshala.json" ]; then
    echo ""
    echo "👉 Copy EVERYTHING between the dashed lines and add as GitHub Secret"
    echo "   Secret name: INTERNSHALA_SESSION"
    echo ""
    echo "──────────────────────────────────────────────────────────────────"
    base64 -w 0 sessions/internshala.json
    echo ""
    echo "──────────────────────────────────────────────────────────────────"
    echo ""
    # Verify it round-trips correctly
    DECODED=$(base64 -w 0 sessions/internshala.json | base64 -d)
    COUNT=$(echo "$DECODED" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else 'ok')" 2>/dev/null || echo "?")
    echo "✅ Verified: $COUNT cookies found in session"
else
    echo ""
    echo "❌ sessions/internshala.json not found."
    echo "   Run:  python setup.py"
    echo "   Then log in to Internshala when the browser opens."
    echo ""
fi

echo ""
echo "=== LINKEDIN SESSION ==="
if [ -f "sessions/linkedin.json" ]; then
    echo ""
    echo "👉 Copy EVERYTHING between the dashed lines and add as GitHub Secret"
    echo "   Secret name: LINKEDIN_SESSION"
    echo ""
    echo "──────────────────────────────────────────────────────────────────"
    base64 -w 0 sessions/linkedin.json
    echo ""
    echo "──────────────────────────────────────────────────────────────────"
    echo ""
    COUNT=$(base64 -w 0 sessions/linkedin.json | base64 -d | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else 'ok')" 2>/dev/null || echo "?")
    echo "✅ Verified: $COUNT cookies found in session"
else
    echo ""
    echo "⚠️  sessions/linkedin.json not found (LinkedIn auto-apply will be disabled)."
    echo "   To enable LinkedIn: run python setup.py and log in to LinkedIn."
    echo ""
fi

echo ""
echo "=== NEXT STEPS ==="
echo "1. Go to: https://github.com/Brijesh-Thakkar/My_Agent/settings/secrets/actions"
echo "2. Click 'New repository secret' for each value above"
echo "3. Add the other required secrets (see .github/SECRETS_SETUP.md)"
echo "4. Push your code:  git add . && git commit -m 'Add GitHub Actions deployment' && git push"
echo "5. Test:  GitHub → Actions tab → 'Intern Hunter' → 'Run workflow' → check Dry Run"
echo ""
