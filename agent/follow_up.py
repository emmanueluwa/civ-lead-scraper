"""
follow up agent - schedules and sends follow up emails
runs on a schedule to check which leads need following up
respects the follow up sequence: day 3, day 7, day 14
stops follow up if prospect replied or unsubscribed
marks leads as cold after final follow up with no response
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from models import get_connection, now_utc, OutreachStatus
from agent.executor import EmailExecutor

logger = logging.getLogger(__name__)

# days after initial email to send each follow up
FOLLOW_UP_SCHEDULE = {
    "follow_up_1": 3,
    "follow_up_2": 7,
    "follow_up_3": 14,
}

# days after final follow up to mark as cold
COLD_AFTER_DAYS = 21


class FollowUpAgent:
    def __init__(self):
        self.executor = EmailExecutor()
        self.calendly_link = os.environ.get("CALENDLY_LINK", "fulodev.com")

    def run(self) -> dict:
        """
        check all sent outreach and send follow ups where due
        returns summary of actions takens
        """
        summary = {
            "follow_up_1_sent": 0,
            "follow_up_2_sent": 0,
            "follow_up_3_sent": 0,
            "marked_cold": 0,
            "errors": 0,
        }

        try:
            self._send_due_follow_ups(summary)
            self._mark_cold_leads(summary)
        except Exception as e:
            logger.error(f"Follow up agent failed: {e}")
            summary["errors"] += 1

        logger.info(
            f"Follow up run complete — "
            f"fu1={summary['follow_up_1_sent']} "
            f"fu2={summary['follow_up_2_sent']} "
            f"fu3={summary['follow_up_3_sent']} "
            f"cold={summary['marked_cold']}"
        )

        return summary

    def _send_due_follow_ups(self, summary: dict) -> None:
        """
        find and send all overdue follow up emails
        """
        now = datetime.now(timezone.utc)

        # follow up 1, sent after 3 days
        fu1_leads = self._get_leads_due_for_follow_up(
            current_status=OutreachStatus.SENT.value,
            sent_field="sent_at",
            days_threshold=FOLLOW_UP_SCHEDULE["follow_up_1"],
        )

        for lead in fu1_leads:
            success = self._send_follow_up(
                lead=lead, follow_up_number=1, email_type="follow_up_1"
            )
            if success:
                summary["follow_up_1_sent"] += 1
            else:
                summary["errors"] += 1

        # follow up 2, follow up 1 sent 4 days ago (7 days since first outreach)
        fu2_leads = self._get_leads_due_for_follow_up(
            current_status=OutreachStatus.FOLLOW_UP_1.value,
            sent_field="follow_up_1_at",
            days_threshold=FOLLOW_UP_SCHEDULE["follow_up_2"]
            - FOLLOW_UP_SCHEDULE["follow_up_1"],
        )

        for lead in fu2_leads:
            success = self._send_follow_up(
                lead=lead, follow_up_number=2, email_type="follow_up_2"
            )
            if success:
                summary["follow_up_2_sent"] += 1
            else:
                summary["errors"] += 1

        # follow up 3, follow up 2 sent 7 days ago (14 days since first outreach)
        fu3_leads = self._get_leads_due_for_follow_up(
            current_status=OutreachStatus.FOLLOW_UP_2.value,
            sent_field="follow_up_2_at",
            days_threshold=FOLLOW_UP_SCHEDULE["follow_up_3"]
            - FOLLOW_UP_SCHEDULE["follow_up_2"],
        )

        for lead in fu3_leads:
            success = self._send_follow_up(
                lead=lead, follow_up_number=3, email_type="follow_up_3"
            )
            if success:
                summary["follow_up_3_sent"] += 1
            else:
                summary["errors"] += 1

    def _get_leads_due_for_follow_up(
        self, current_status: str, sent_field: str, days_threshold: int
    ) -> list:
        """
        get outreach records due for follow up
        only returns leads where the required number of days have passed
        excludes replied, interested, booked and unsubscribed leads
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days_threshold)
        ).isoformat()

        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, place_id, company_name, recipient_email,
                       recipient_name, email_subject, email_body,
                       youtube_url, {sent_field}
                FROM outreach
                WHERE status = ?
                AND {sent_field} <= ?
                AND recipient_email IS NOT NULL
                ORDER BY {sent_field} ASC
                """,
                (current_status, cutoff),
            ).fetchall()

        return [dict(row) for row in rows]

    def _send_follow_up(
        self, lead: dict, follow_up_number: int, email_type: str
    ) -> bool:
        """
        send a follow up email for a lead
        """
        subject = f"Re: {lead['email_subject']}"
        body = self._build_follow_up_body(
            company_name=lead["company_name"],
            recipient_name=lead["recipient_name"],
            original_body=lead["email_body"],
            youtube_url=lead["youtube_url"],
            follow_up_number=follow_up_number,
        )

        success = self.executor.send(
            outreach_id=lead["id"],
            recipient_email=lead["recipient_email"],
            recipient_name=lead["recipient_name"],
            subject=subject,
            body=body,
            email_type=email_type,
        )

        if success:
            logger.info(
                f"Follow up {follow_up_number} sent to "
                f"{lead['recipient_email']} at {lead['company_name']}"
            )

        return success

    def _build_follow_up_body(
        self,
        company_name: str,
        recipient_name: Optional[str],
        original_body: str,
        youtube_url: Optional[str],
        follow_up_number: int,
    ) -> str:
        """
        build a short, human follow up email body
        each follow up has a different angle
        """
        first_name = recipient_name.split()[0] if recipient_name else "there"

        if follow_up_number == 1:
            return (
                f"Hi {first_name},\n\n"
                f"Just wanted to make sure my previous email didn't get buried.\n\n"
                f"I built SwiftCiv specifically for civil engineers who spend time "
                f"hunting through regulatory documents — it gives verbatim quotes "
                f"with exact page citations instantly.\n\n"
                f"Reply and I'll set you up with free access so you can try it on "
                f"your own documents. Or book a quick 10-minute call here: "
                f"{self.calendly_link}\n\n"
                f"Emmanuel\n"
                f"swiftciv.com"
            )

        elif follow_up_number == 2:
            video_line = (
                f"I recorded a short demo using documents relevant to your work: "
                f"{youtube_url}\n\n"
                if youtube_url
                else ""
            )
            return (
                f"Hi {first_name},\n\n"
                f"One more follow up.\n\n"
                f"{video_line}"
                f"Beyond SwiftCiv, I also work with civil engineering firms to build "
                f"custom AI tools around their specific workflows — document comparison, "
                f"spec generation, internal knowledge bases, whatever slows your team down.\n\n"
                f"Reply and I'll set up free access to SwiftCiv so you can try it on "
                f"your own documents. Or book a call to discuss what we could build "
                f"for {company_name}: {self.calendly_link}\n\n"
                f"Emmanuel\n"
                f"swiftciv.com"
            )

        else:  # follow_up_number == 3
            return (
                f"Hi {first_name},\n\n"
                f"This is my last email — I don't want to clog your inbox.\n\n"
                f"Whether it's SwiftCiv for instant regulatory lookups or a bespoke AI "
                f"tool built around how {company_name} works — the offer stands whenever "
                f"the time is right.\n\n"
                f"Reply any time and I'll set up free access to SwiftCiv. "
                f"Or book a call: "
                f"{self.calendly_link}\n\n"
                f"Wishing you and the team well.\n\n"
                f"Emmanuel\n"
                f"swiftciv.com"
            )

    def _mark_cold_leads(self, summary: dict) -> None:
        """
        mark leads as cold if initial email was sent 21 or more days ago
        7 days after follow-up 3
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        with get_connection() as conn:
            result = conn.execute(
                """
                UPDATE outreach
                SET status = 'cold',
                    cold_at = ?,
                    updated_at = ?
                WHERE status = 'follow_up_3'
                AND follow_up_3_at <= ?
                """,
                (now_utc(), now_utc(), cutoff),
            )
            count = result.rowcount

        if count > 0:
            summary["marked_cold"] += count
            logger.info(f"Marked {count} leads as cold")
