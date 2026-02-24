#!/bin/bash
# Wrapper script for cron — runs the Notion CRM cron runner
# macOS requires Full Disk Access for cron. This script uses the user's
# Python and sets the working directory properly.

export PATH="/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin:/usr/local/bin:/usr/bin:/bin"

cd "/Users/anthonynagle/Documents/Agentic Workflows/Livite Sports Outreach" || exit 1

python3 tools/notion_cron_runner.py >> .tmp/cron.log 2>&1
