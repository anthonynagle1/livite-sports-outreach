"""Shared Gmail client with OAuth2 for reading emails and attachments.

Extracted from tools/catering/forkable.py for reuse across email integrations
(Forkable orders, vendor invoices, etc.).
"""
from __future__ import annotations

import base64
import logging
import os
import re

logger = logging.getLogger(__name__)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "").strip()


class GmailClient:
    """Gmail API client with OAuth2 credential management."""

    def __init__(self):
        self._service = None
        self._credentials = None

    def _get_credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if self._credentials and self._credentials.valid:
            return self._credentials

        self._credentials = Credentials(
            token=None,
            refresh_token=GOOGLE_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
        )
        if self._credentials.expired or not self._credentials.token:
            self._credentials.refresh(Request())
            logger.info("Gmail credentials refreshed")
        return self._credentials

    def _get_service(self):
        if self._service is None:
            from googleapiclient.discovery import build
            self._service = build("gmail", "v1", credentials=self._get_credentials())
        return self._service

    # ── Send ──

    def send_html(self, to: list[str], subject: str, html_body: str,
                  plain_body: str = "") -> dict:
        """Send an HTML email via Gmail API.

        Args:
            to: List of recipient email addresses.
            subject: Email subject line.
            html_body: HTML content.
            plain_body: Optional plain text fallback.

        Returns:
            Dict with 'ok', 'message_id', and 'error' (if failed).
        """
        # Build raw MIME message without email.mime (which is shadowed by
        # this package). Construct the multipart/alternative manually.
        boundary = "----livite-boundary-9f8e7d"
        parts = []
        if plain_body:
            parts.append(
                f"--{boundary}\r\n"
                f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
                f"{plain_body}\r\n"
            )
        parts.append(
            f"--{boundary}\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n\r\n"
            f"{html_body}\r\n"
        )
        parts.append(f"--{boundary}--\r\n")

        raw_msg = (
            f"To: {', '.join(to)}\r\n"
            f"Subject: {subject}\r\n"
            f"MIME-Version: 1.0\r\n"
            f"Content-Type: multipart/alternative; boundary=\"{boundary}\"\r\n"
            f"\r\n"
            + "".join(parts)
        )

        raw = base64.urlsafe_b64encode(raw_msg.encode("utf-8")).decode("utf-8")
        svc = self._get_service()
        try:
            result = svc.users().messages().send(
                userId="me", body={"raw": raw},
            ).execute()
            logger.info("Email sent to %s (id=%s)", ", ".join(to), result.get("id"))
            return {"ok": True, "message_id": result.get("id"), "recipients": to}
        except Exception as e:
            logger.error("Email send failed: %s", e)
            return {"ok": False, "error": str(e)}

    # ── Search / Fetch ──

    def search_emails(self, query: str, max_results: int = 10) -> list[dict]:
        """Search Gmail for emails matching a query string.

        Args:
            query: Gmail search query (e.g., "is:unread from:baldor subject:invoice")
            max_results: Maximum emails to return.

        Returns:
            List of email dicts with id, subject, from_email, body_text, attachments, etc.
        """
        svc = self._get_service()
        try:
            results = svc.users().messages().list(
                userId="me", q=query, maxResults=max_results,
            ).execute()
            messages = results.get("messages", [])
            emails = []
            for msg_ref in messages:
                try:
                    data = self._get_details(msg_ref["id"])
                    if data:
                        emails.append(data)
                except Exception as e:
                    logger.error("Error fetching email %s: %s", msg_ref["id"], e)
            return emails
        except Exception as e:
            logger.error("Error searching emails: %s", e)
            return []

    def get_unread_from(self, sender: str, subject_contains: str = "",
                        max_results: int = 10) -> list[dict]:
        """Fetch unread emails from a specific sender, optionally filtered by subject."""
        query = f"is:unread in:inbox from:{sender}"
        if subject_contains:
            query += f" subject:{subject_contains}"
        return self.search_emails(query, max_results)

    def _get_details(self, message_id: str) -> dict | None:
        svc = self._get_service()
        msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}

        from_raw = headers.get("from", "")
        from_name, from_email = "", from_raw
        if "<" in from_raw:
            from_name = from_raw[:from_raw.index("<")].strip().strip('"')
            from_email = from_raw[from_raw.index("<") + 1:from_raw.index(">")].strip()

        body_text = self._extract_body(msg.get("payload", {}))
        attachments = self._extract_attachments(msg.get("payload", {}))

        return {
            "id": message_id,
            "thread_id": msg.get("threadId", ""),
            "from_email": from_email,
            "from_name": from_name,
            "subject": headers.get("subject", "(no subject)"),
            "body_text": body_text,
            "date": headers.get("date", ""),
            "attachments": attachments,
        }

    # ── Attachments ──

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Download an email attachment by its attachment ID.

        Returns raw bytes of the attachment.
        """
        svc = self._get_service()
        result = svc.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id,
        ).execute()
        data = result.get("data", "")
        return base64.urlsafe_b64decode(data)

    # ── Labels / Read Status ──

    def mark_as_read(self, message_id: str) -> bool:
        svc = self._get_service()
        try:
            svc.users().messages().modify(
                userId="me", id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
            return True
        except Exception as e:
            logger.error("Error marking message as read: %s", e)
            return False

    def add_label(self, message_id: str, label_name: str) -> bool:
        svc = self._get_service()
        try:
            label_id = self._get_or_create_label(label_name)
            if not label_id:
                return False
            svc.users().messages().modify(
                userId="me", id=message_id,
                body={"addLabelIds": [label_id]},
            ).execute()
            return True
        except Exception as e:
            logger.error("Error adding label: %s", e)
            return False

    def _get_or_create_label(self, label_name: str) -> str | None:
        svc = self._get_service()
        try:
            results = svc.users().labels().list(userId="me").execute()
            for label in results.get("labels", []):
                if label["name"] == label_name:
                    return label["id"]
            new_label = svc.users().labels().create(
                userId="me",
                body={"name": label_name, "labelListVisibility": "labelShow",
                      "messageListVisibility": "show"},
            ).execute()
            return new_label["id"]
        except Exception as e:
            logger.error("Error with label: %s", e)
            return None

    # ── Body extraction helpers ──

    def _extract_body(self, payload: dict) -> str:
        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            for nested in part.get("parts", []):
                if nested.get("mimeType") == "text/plain":
                    data = nested.get("body", {}).get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        # Fallback: strip HTML
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    return _strip_html(html)
        return ""

    def _extract_attachments(self, payload: dict) -> list[dict]:
        attachments = []

        def _scan(parts):
            for part in parts:
                fn = part.get("filename", "")
                body = part.get("body", {})
                if fn and body.get("attachmentId"):
                    attachments.append({
                        "filename": fn,
                        "mime_type": part.get("mimeType", ""),
                        "size": body.get("size", 0),
                        "attachment_id": body["attachmentId"],
                        "message_id": "",  # filled by caller if needed
                    })
                if part.get("parts"):
                    _scan(part["parts"])

        _scan(payload.get("parts", []))
        return attachments


def _strip_html(html: str) -> str:
    """Basic HTML to text conversion."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "head", "meta", "link"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line)
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", text).strip()
