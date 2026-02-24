#!/bin/bash
# Deploy Livite Sports email automation to Raspberry Pi
# Run from Mac: bash deploy_to_pi.sh

set -e

PI_HOST="anthonyn1644@192.168.0.80"
PI_DIR="/home/anthonyn1644/livite-sports"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Deploying Livite Sports to Pi ==="
echo "From: $PROJECT_DIR"
echo "To:   $PI_HOST:$PI_DIR"
echo ""

# Step 1: Create directory structure on Pi
echo "[1/6] Creating directories on Pi..."
ssh $PI_HOST "mkdir -p $PI_DIR/tools $PI_DIR/.tmp"

# Step 2: Copy tool files (only what's needed for automation)
echo "[2/6] Copying automation tools..."
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
        echo "  Copied: $tool"
    else
        echo "  SKIP (not found): $tool"
    fi
done

# Step 3: Copy config files
echo "[3/6] Copying configuration..."
scp -q "$PROJECT_DIR/requirements-pi.txt" "$PI_HOST:$PI_DIR/requirements.txt"

# Step 4: Copy credentials (sensitive - use scp, not git)
echo "[4/6] Copying credentials..."
scp -q "$PROJECT_DIR/.env" "$PI_HOST:$PI_DIR/"
scp -q "$PROJECT_DIR/credentials.json" "$PI_HOST:$PI_DIR/"
scp -q "$PROJECT_DIR/token.json" "$PI_HOST:$PI_DIR/"

# Step 5: Set up log rotation on Pi
echo "[5/7] Setting up log rotation..."
ssh $PI_HOST << 'REMOTE'
sudo tee /etc/logrotate.d/livite-sports > /dev/null << 'LOGROTATE'
/home/anthonyn1644/livite-sports/.tmp/cron.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
LOGROTATE
echo "  Log rotation configured"
REMOTE

# Step 6: Set up Python venv and install deps on Pi
echo "[6/7] Setting up Python environment on Pi..."
ssh $PI_HOST << 'REMOTE'
cd /home/anthonyn1644/livite-sports
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  Python packages installed successfully"
REMOTE

# Step 7: Test the setup
echo "[7/7] Testing setup..."
ssh $PI_HOST << 'REMOTE'
cd /home/anthonyn1644/livite-sports
source venv/bin/activate

# Test Notion
python3 -c "
from notion_client import Client
from dotenv import load_dotenv
import os
load_dotenv()
notion = Client(auth=os.getenv('NOTION_API_KEY'))
# Quick query to verify connection
notion.databases.retrieve(database_id=os.getenv('NOTION_EMAIL_QUEUE_DB'))
print('  Notion API: OK')
" 2>/dev/null

# Test Gmail
python3 -c "
import sys
sys.path.insert(0, 'tools')
from notion_send_gmail import get_gmail_credentials
creds = get_gmail_credentials()
print(f'  Gmail OAuth: OK (valid={creds.valid})')
" 2>/dev/null
REMOTE

echo ""
echo "=== Deployment complete! ==="
echo ""
echo "Next steps:"
echo "  1. SSH to Pi:  ssh $PI_HOST"
echo "  2. Set up cron: crontab -e"
echo "  3. Add this line:"
echo "     */5 * * * * cd $PI_DIR && $PI_DIR/venv/bin/python3 tools/notion_cron_runner.py >> .tmp/cron.log 2>&1"
echo "  (Log rotation is handled by /etc/logrotate.d/livite-sports)"
