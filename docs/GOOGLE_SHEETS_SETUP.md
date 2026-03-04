# Google Sheets API Setup Guide

This guide will help you set up Google Sheets API access for the NCAA Contact Automation system.

## Step 1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Sign in with your Google account
3. Click "Select a project" → "New Project"
4. Name your project: "NCAA Contact Automation"
5. Click "Create"

## Step 2: Enable Google Sheets API

1. In the Google Cloud Console, go to **APIs & Services** → **Library**
2. Search for "Google Sheets API"
3. Click on "Google Sheets API"
4. Click "Enable"

## Step 3: Create OAuth 2.0 Credentials

1. Go to **APIs & Services** → **Credentials**
2. Click "Create Credentials" → "OAuth client ID"
3. If prompted, configure the OAuth consent screen:
   - User Type: **External**
   - App name: "NCAA Contact Automation"
   - User support email: Your email
   - Developer contact: Your email
   - Click "Save and Continue"
   - Scopes: Skip (click "Save and Continue")
   - Test users: Add your email
   - Click "Save and Continue"
4. Back to "Create OAuth client ID":
   - Application type: **Desktop app**
   - Name: "NCAA Contact Automation Desktop"
   - Click "Create"
5. Download the credentials:
   - Click "Download JSON"
   - Save the file as `credentials.json` in your project root directory

## Step 4: Place Credentials File

Move the downloaded `credentials.json` file to your project directory:

```bash
# Should be at:
/Users/anthonynagle/Documents/Agentic Workflows/Livite Sports Outreach/credentials.json
```

## Step 5: First-Time Authentication

The first time you run the export tool, it will:
1. Open a browser window
2. Ask you to sign in with your Google account
3. Request permission to manage your Google Sheets
4. Save a `token.pickle` file for future use

**After the first time, you won't need to authenticate again** (unless you delete `token.pickle`).

## Step 6: Test the Setup

Run a test export:

```bash
python3 tools/export_to_sheets.py \
  --input .tmp/validated_bc_vs_merrimack.json \
  --spreadsheet-name "Test - BC Baseball Contacts"
```

If successful, you'll see:
- Browser window opens for authentication (first time only)
- Spreadsheet created with 3 tabs
- URL printed: `https://docs.google.com/spreadsheets/d/...`

## Troubleshooting

### Error: "credentials.json not found"
- Make sure you downloaded the OAuth credentials (not service account credentials)
- Place the file in the project root directory

### Error: "Access blocked: This app's request is invalid"
- Make sure you selected "Desktop app" (not "Web application")
- Try creating new credentials

### Error: "The user has not granted the app..."
- Add your email to the "Test users" list in OAuth consent screen
- Make sure the app is in "Testing" mode (not "Production")

## Security Notes

- **Never commit `credentials.json` to git** (it's already in `.gitignore`)
- **Never commit `token.pickle` to git** (it's already in `.gitignore`)
- These files contain sensitive authentication data
- If accidentally exposed, delete the credentials in Google Cloud Console and create new ones

## What Gets Created

The export tool creates a Google Spreadsheet with 3 tabs:

1. **Game-by-Game Contacts** - Primary view with all game details and matched contacts
2. **Master Contacts Cache** - All staff members discovered across all schools
3. **Chronological View** - Games sorted by date for calendar planning

You can then share this spreadsheet with your team or use it for catering outreach coordination.
