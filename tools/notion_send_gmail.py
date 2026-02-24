#!/usr/bin/env python3
"""
Tool: notion_send_gmail.py
Purpose: Send emails via Gmail API using existing Google OAuth credentials

This replaces SendGrid with Gmail for simpler setup (no new account needed).
Uses the same credentials.json and token.json as Google Sheets export.

Usage:
    # Send a single email
    python tools/notion_send_gmail.py \
        --to coach@school.edu \
        --subject "Catering for your game" \
        --body "Hi Coach..."

    # Test connection (sends test email to yourself)
    python tools/notion_send_gmail.py --test

Required:
    - credentials.json (Google OAuth client)
    - Gmail API enabled in Google Cloud Console
    - gmail.send scope in OAuth consent

First run will open browser for authentication if token.json doesn't have Gmail scope.
"""

import argparse
import base64
import json
import os
import pickle
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    print("Error: Google API packages not installed.", file=sys.stderr)
    print("Run: pip install google-auth google-auth-oauthlib google-api-python-client", file=sys.stderr)
    sys.exit(1)

from dotenv import load_dotenv

load_dotenv()

# Gmail API scopes
SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',  # For getting user's email in test
    'https://www.googleapis.com/auth/spreadsheets'  # Keep Sheets scope too
]


def get_gmail_credentials():
    """
    Get Gmail API credentials, handling OAuth flow if needed.
    Uses same credentials.json as Sheets export.
    """
    creds = None
    creds_path = os.getenv('GOOGLE_SHEETS_CREDENTIALS_PATH', 'credentials.json')
    token_path = os.getenv('GOOGLE_SHEETS_TOKEN_PATH', 'token.json')

    # Check for existing token
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception as e:
            print(f"Warning: Could not load token: {e}", file=sys.stderr)

    # Check if creds are valid or need refresh
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Token refresh failed: {e}", file=sys.stderr)
                creds = None

        if not creds:
            # Need to run OAuth flow
            if not os.path.exists(creds_path):
                print(f"Error: {creds_path} not found", file=sys.stderr)
                print("\nTo set up Gmail API:", file=sys.stderr)
                print("1. Go to Google Cloud Console", file=sys.stderr)
                print("2. Enable Gmail API", file=sys.stderr)
                print("3. Download OAuth credentials as credentials.json", file=sys.stderr)
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save the credentials for next run
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
        print(f"Credentials saved to {token_path}", file=sys.stderr)

    return creds


def get_gmail_service():
    """Get authenticated Gmail API service."""
    creds = get_gmail_credentials()
    return build('gmail', 'v1', credentials=creds)


def create_message(to_email, subject, body, from_name=None):
    """Create a MIME message for sending."""
    message = MIMEMultipart('alternative')
    message['to'] = to_email
    message['subject'] = subject

    # Set From address using env var (must be verified alias in Gmail)
    from_email = os.getenv('FROM_EMAIL', '')
    from_display = os.getenv('FROM_NAME', '')
    if from_email:
        if from_display:
            message['from'] = f'{from_display} <{from_email}>'
        else:
            message['from'] = from_email

    # Add plain text body
    text_part = MIMEText(body, 'plain')
    message.attach(text_part)

    return message


def send_email(to_email, subject, body, from_name=None):
    """
    Send an email via Gmail API.

    Args:
        to_email: Recipient email address
        subject: Email subject line
        body: Email body (plain text)
        from_name: Optional display name (Gmail uses authenticated user's address)

    Returns:
        dict with 'success' and 'message_id' or 'error'
    """
    try:
        service = get_gmail_service()

        # Create the message
        message = create_message(to_email, subject, body, from_name)

        # Encode for Gmail API
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')

        # Send it
        result = service.users().messages().send(
            userId='me',
            body={'raw': raw}
        ).execute()

        message_id = result.get('id', '')
        thread_id = result.get('threadId', '')
        print(f"Email sent successfully. Message ID: {message_id}, Thread ID: {thread_id}", file=sys.stderr)

        return {
            'success': True,
            'message_id': message_id,
            'thread_id': thread_id,
            'to': to_email,
            'subject': subject
        }

    except HttpError as e:
        error_msg = str(e)
        print(f"Gmail API error: {error_msg}", file=sys.stderr)
        return {
            'success': False,
            'error': error_msg,
            'to': to_email
        }

    except Exception as e:
        error_msg = str(e)
        print(f"Error sending email: {error_msg}", file=sys.stderr)
        return {
            'success': False,
            'error': error_msg,
            'to': to_email
        }


def send_test_email():
    """Send a test email to yourself to verify setup."""
    try:
        service = get_gmail_service()

        # Get the authenticated user's email
        profile = service.users().getProfile(userId='me').execute()
        my_email = profile['emailAddress']

        print(f"Sending test email to: {my_email}", file=sys.stderr)

        result = send_email(
            to_email=my_email,
            subject="[TEST] Livite Sports CRM - Gmail Integration Working!",
            body="""This is a test email from the Livite Sports CRM.

If you're seeing this, Gmail API integration is working correctly.

The system can now:
- Create email drafts in Notion
- Send approved emails via your Gmail
- Track outreach in the CRM

You can delete this email.

-- Livite Sports Outreach System
"""
        )

        return result

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def main():
    parser = argparse.ArgumentParser(
        description="Send emails via Gmail API"
    )
    parser.add_argument(
        "--to",
        help="Recipient email address"
    )
    parser.add_argument(
        "--subject",
        help="Email subject line"
    )
    parser.add_argument(
        "--body",
        help="Email body text"
    )
    parser.add_argument(
        "--body-file",
        help="Read body from file instead"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Send a test email to yourself"
    )

    args = parser.parse_args()

    if args.test:
        print("Testing Gmail API connection...", file=sys.stderr)
        result = send_test_email()
        print(json.dumps(result, indent=2))
        sys.exit(0 if result['success'] else 1)

    if not args.to or not args.subject:
        print("Error: --to and --subject required (or use --test)", file=sys.stderr)
        sys.exit(1)

    # Get body from file or argument
    body = args.body or ""
    if args.body_file:
        try:
            with open(args.body_file, 'r') as f:
                body = f.read()
        except Exception as e:
            print(f"Error reading body file: {e}", file=sys.stderr)
            sys.exit(1)

    if not body:
        print("Error: --body or --body-file required", file=sys.stderr)
        sys.exit(1)

    # Send the email
    result = send_email(args.to, args.subject, body)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result['success'] else 1)


if __name__ == "__main__":
    main()
