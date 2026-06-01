"""
email executor - sends personalised cold emails via gmail API
OAuth2 refresh token for fully automated sending.
logs every sent email to the database for audit trail
respects daily sending limits to protect domain reputation
"""

import base64
import logging
import os
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from models import get_connection, now_utc

logger = logging.getLogger(__name__)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

# daily sending limit — increase gradually as domain warms up
# week 1-2: 50/day, week 3-4: 100/day, week 5+: 300/day
DAILY_SENDING_LIMIT = 50


class EmailExecutor:
    def __init__(self):
        self.sender_email = os.environ.get("GMAIL_SENDER_EMAIL")
        if not self.sender_email:
            raise ValueError("GMAIL_SENDER_EMAIL not set in environment")

        self.service = self._build_gmail_service()

    def _build_gmail_service(self):
        """
        build authenticated gmail service using OAuth2 refresh token
        """
        creds = Credentials(
            token=None,
            refresh_token=os.environ.get("GMAIL_REFRESH_TOKEN"),
            client_id=os.environ.get("GMAIL_CLIENT_ID"),
            client_secret=os.environ.get("GMAIL_CLIENT_SECRET"),
            token_uri="https://oauth2.googleapis.com/token",
            scopes=GMAIL_SCOPES,
        )

        # refresh the token
        creds.refresh(Request())

        return build("gmail", "v1", credentials=creds)

    def get_daily_sent_count(self) -> int:
        """
        return number of emails sent today
        """
        today = now_utc()[:10]  # YYYY-MM-DD
        with get_connection() as conn:
            count = conn.execute(
                """
                SELECT COUNT(*) FROM email_log
                WHERE sent_at LIKE ?
                AND email_type = 'initial'
                """,
                (f"{today}%",),
            ).fetchone()[0]
        return count

    def is_daily_limit_reached(self) -> bool:
        """
        check if daily sending limit has been reached.
        """
        return self.get_daily_sent_count() >= DAILY_SENDING_LIMIT

    def send(
        self,
        outreach_id: int,
        recipient_email: str,
        recipient_name: Optional[str],
        subject: str,
        body: str,
        email_type: str = "initial",
    ) -> bool:
        """
        send an email via gmail api
        logs the email to db
        returns True on success, False on failure
        """
        if not recipient_email:
            logger.warning(f"No recipient email for outreach {outreach_id} — skipping")

            return False

        if email_type == "initial" and self.is_daily_limit_reached():
            logger.info(
                f"Daily sending limit of {DAILY_SENDING_LIMIT} reached — "
                f"skipping outreach {outreach_id}"
            )
            return False

        try:
            message = self._build_message(
                recipient_email=recipient_email,
                recipient_name=recipient_name,
                subject=subject,
                body=body,
            )

            sent = (
                self.service.users()
                .messages()
                .send(
                    userId="me",
                    body=message,
                )
                .execute()
            )

            gmail_message_id = sent.get("id")

            self._log_email(
                outreach_id=outreach_id,
                email_type=email_type,
                recipient=recipient_email,
                subject=subject,
                body=body,
                gmail_message_id=gmail_message_id,
            )

            self._update_outreach_status(
                outreach_id=outreach_id,
                email_type=email_type,
            )

            logger.info(
                f"Email sent — type={email_type} "
                f"to={recipient_email} "
                f"subject={subject[:50]}... "
                f"gmail_id={gmail_message_id}"
            )
            return True

        except HttpError as e:
            logger.error(f"Gmail API error sending to {recipient_email}: {e}")
            return False

        except Exception as e:
            logger.error(f"Unexpected error sending to {recipient_email}: {e}")
            return False

    def _build_message(
        self,
        recipient_email: str,
        recipient_name: Optional[str],
        subject: str,
        body: str,
    ) -> dict:
        """
        build a gmail API message object.
        """
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"Emmanuel Uwadiae <{self.sender_email}>"

        if recipient_name:
            message["To"] = f"{recipient_name} <{recipient_email}>"
        else:
            message["To"] = recipient_email

        # plain text version
        text_part = MIMEText(body, "plain")

        # html version — preserves line breaks and makes links clickable
        html_body = body.replace("\n", "<br>")
        html_body = self._linkify(html_body)
        html_part = MIMEText(
            f"<html><body style='font-family:Arial,sans-serif;font-size:14px;'>"
            f"{html_body}"
            f"</body></html>",
            "html",
        )

        message.attach(text_part)
        message.attach(html_part)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        return {"raw": raw}

    def _linkify(self, text: str) -> str:
        """
        convert YouTube URLs to clickable links in HTML.
        """

        url_pattern = re.compile(r"(https?://(?:www\.)?(?:youtube\.com|youtu\.be)\S+)")
        return url_pattern.sub(
            r'<a href="\1" style="color:#1a73e8;">\1</a>',
            text,
        )

    def _log_email(
        self,
        outreach_id: int,
        email_type: str,
        recipient: str,
        subject: str,
        body: str,
        gmail_message_id: Optional[str],
    ) -> None:
        """
        log sent email to database for audit trail.
        """
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO email_log (
                    outreach_id, email_type, recipient,
                    subject, body, gmail_message_id, sent_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outreach_id,
                    email_type,
                    recipient,
                    subject,
                    body,
                    gmail_message_id,
                    now_utc(),
                ),
            )

    def _update_outreach_status(
        self,
        outreach_id: int,
        email_type: str,
    ) -> None:
        """
        update outreach status and timestamp after sending.
        """
        status_map = {
            "initial": ("sent", "sent_at"),
            "follow_up_1": ("follow_up_1", "follow_up_1_at"),
            "follow_up_2": ("follow_up_2", "follow_up_2_at"),
            "follow_up_3": ("follow_up_3", "follow_up_3_at"),
        }

        status, timestamp_field = status_map.get(email_type, ("sent", "sent_at"))

        with get_connection() as conn:
            conn.execute(
                f"""
                UPDATE outreach
                SET status = ?,
                    {timestamp_field} = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, now_utc(), now_utc(), outreach_id),
            )
