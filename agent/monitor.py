"""
email monitor - watches gmail inbox for replies to outreach emails
runs on a schedule to detect replies and update outreach status
pauses follow up sequence when a reply is detected
flags interested prospects for the booking agent
"""

import logging
import os
import base64
from datetime import datetime, timezone, timedelta
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from models import get_connection, now_utc, OutreachStatus

logger = logging.getLogger(__name__)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

# keywords that indicate a prospect is interested
INTEREST_SIGNALS = [
    "interested",
    "tell me more",
    "sounds good",
    "let's chat",
    "lets chat",
    "book a call",
    "schedule a call",
    "demo",
    "more information",
    "how does it work",
    "pricing",
    "cost",
    "how much",
    "free trial",
    "sign up",
    "calendly",
    "available",
    "when can",
    "good time",
]

# keywords that indicate a prospect wants to unsubscribe
UNSUBSCRIBE_SIGNALS = [
    "unsubscribe",
    "remove me",
    "stop emailing",
    "not interested",
    "do not contact",
    "don't contact",
    "take me off",
    "opt out",
]


class EmailMonitor:
    def __init__(self):
        self.service = self._build_gmail_service()

    def _build_gmail_service(self):
        """
        build authenticated Gmail service using OAuth2 refresh token.
        """
        creds = Credentials(
            token=None,
            refresh_token=os.environ.get("GMAIL_REFRESH_TOKEN"),
            client_id=os.environ.get("GMAIL_CLIENT_ID"),
            client_secret=os.environ.get("GMAIL_CLIENT_SECRET"),
            token_uri="https://oauth2.googleapis.com/token",
            scopes=GMAIL_SCOPES,
        )
        creds.refresh(Request())
        return build("gmail", "v1", credentials=creds)

    def check_replies(self) -> dict:
        """
        check gmail inbox for replies to outreach emails
        updates outreach status for any replies found
        returns summary of what was found
        """
        summary = {
            "replies_found": 0,
            "interested": 0,
            "unsubscribed": 0,
            "errors": 0,
        }

        try:
            sent_outreach = self._get_sent_outreach()
            if not sent_outreach:
                logger.info("no sent outreach to check for replies")

                return summary

            # check outreach for replies
            for outreach in sent_outreach:
                try:
                    reply = self._find_reply(
                        recipient_email=outreach["recipient_email"],
                        subject=outreach["email_subject"],
                        sent_at=outreach["sent_at"],
                    )

                    if reply:
                        summary["replies_found"] += 1
                        intent = self._classify_intent(reply["body"])

                        self._update_outreach_on_reply(
                            outreach_id=outreach["id"],
                            reply_content=reply["body"],
                            intent=intent,
                            received_at=reply["received_at"],
                        )

                        if intent == "interested":
                            summary["interested"] += 1
                            logger.info(
                                f"Interested reply from {outreach['recipient_email']} "
                                f"at {outreach['company_name']}"
                            )
                        elif intent == "unsubscribe":
                            summary["unsubscribed"] += 1
                            logger.info(
                                f"Unsubscribe request from {outreach['recipient_email']}"
                            )

                except Exception as e:
                    logger.error(
                        f"Error checking reply for outreach " f"{outreach['id']}: {e}"
                    )
                    summary["errors"] += 1

        except Exception as e:
            logger.error(f"Email monitor failed: {e}")
            summary["errors"] += 1

        logger.info(
            f"Reply check complete — "
            f"found={summary['replies_found']} "
            f"interested={summary['interested']} "
            f"unsubscribed={summary['unsubscribed']}"
        )

        return summary

    def _get_sent_outreach(self) -> list:
        """
        get all outreach records that have been sent and not yet replied to.
        """
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, place_id, company_name, recipient_email,
                       email_subject, sent_at, status
                FROM outreach
                WHERE status IN ('sent', 'follow_up_1', 'follow_up_2', 'follow_up_3')
                AND recipient_email IS NOT NULL
                ORDER BY sent_at DESC
                """,
            ).fetchall()

        return [dict(row) for row in rows]

    def _find_reply(
        self,
        recipient_email: str,
        subject: str,
        sent_at: str,
    ) -> Optional[dict]:
        """
        search gmail inbox for a reply from a specific email address
        only looks for emails received after the outreach was sent
        """
        try:
            # search for email from the following address
            query = f"from:{recipient_email} in:inbox"

            result = (
                self.service.users()
                .messages()
                .list(userId="me", q=query, maxResults=5)
                .execute()
            )

            messages = result.get("messages", [])

            for message in messages:
                msg = (
                    self.service.users()
                    .messages()
                    .get(userId="me", id=message["id"], format="full")
                    .execute()
                )

                # check if this email was received after our outreach
                internal_date = int(msg.get("internalDate", 0)) / 1000
                received_at = datetime.fromtimestamp(internal_date, tz=timezone.utc)
                sent_datetime = datetime.fromisoformat(sent_at)

                if received_at > sent_datetime:
                    body = self._extract_body(msg)
                    return {
                        "gmail_id": message["id"],
                        "received_at": received_at.isoformat(),
                        "body": body,
                    }

        except HttpError as e:
            logger.error(f"gmail API error searching for reply: {e}")

        return None

    def _extract_body(self, message: dict) -> str:
        """
        extract plain text body from gmail message
        """
        try:
            payload = message.get("payload", {})

            # single part message
            if "body" in payload and payload["body"].get("data"):
                data = payload["body"]["data"]
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

            # multipart message
            parts = payload.get("parts", [])
            for part in parts:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data).decode(
                            "utf-8", errors="ignore"
                        )

        except Exception as e:
            logger.warning(f"Failed to extract email body: {e}")

        return ""

    def _classify_intent(self, body: str) -> str:
        """
        classify reply intent as interested, unsubscribe or neutral
        """
        body_lower = body.lower()

        for signal in UNSUBSCRIBE_SIGNALS:
            if signal in body_lower:
                return "unsubscribe"

        for signal in INTEREST_SIGNALS:
            if signal in body_lower:
                return "interested"

        return "neutral"

    def _update_outreach_on_reply(
        self, outreach_id: int, reply_content: str, intent: str, received_at: str
    ) -> None:
        """
        update outreach record when a reply is detected
        """
        if intent == "interested":
            new_status = OutreachStatus.INTERESTED.value
        elif intent == "unsubscribe":
            new_status = OutreachStatus.UNSUBSCRIBED.value
        else:
            new_status = OutreachStatus.REPLIED.value

        with get_connection() as conn:
            conn.execute(
                """
                UPDATE outreach
                SET status = ?,
                    replied_at = ?,
                    reply_content = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    new_status,
                    received_at,
                    reply_content[:2000],  # truncate long replies
                    now_utc(),
                    outreach_id,
                ),
            )
