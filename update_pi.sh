#!/bin/bash
# Push code updates to Pi (no credential changes)
# Run from Mac: bash update_pi.sh

set -e

PI_HOST="anthonyn1644@192.168.0.80"
PI_DIR="/home/anthonyn1644/livite-sports"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Updating automation tools on Pi..."

TOOLS=(
    "notion_cron_runner.py"
    "notion_draft_email.py"
    "notion_process_approved.py"
    "notion_send_gmail.py"
    "check_gmail_responses.py"
    "notion_sync_followups.py"
    "notion_log_response.py"
    "notion_daily_digest.py"
    "notion_update_lead_scores.py"
    "notion_convert_to_order.py"
    "notion_local_dashboard.py"
)

for tool in "${TOOLS[@]}"; do
    if [ -f "$PROJECT_DIR/tools/$tool" ]; then
        scp -q "$PROJECT_DIR/tools/$tool" "$PI_HOST:$PI_DIR/tools/"
        echo "  Updated: $tool"
    fi
done

echo "Update complete."
