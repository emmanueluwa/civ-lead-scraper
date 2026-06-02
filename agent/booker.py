"""
booking agent - detects interest in replies and sends calendly links
monitors for calendly webhook events when calls are booked
sends telegram alert when a call is confirmed
updates outreach and booked_calls tabels when needed
"""

import logging
import os
from typing import Optional

import requests

from models import get_connection, now_utc
from scraper.notifier import Notifier
from agent.executor import EmailExecutor

logger = logging.getLogger(__name__)


class BookingAgent:
    def __init__(self):
        self.calendly_link = os.environ.get("CALENDLY_LINK")
        if not self.calendly_link:
            raise ValueError("CALENDLY_LINK not set in environment")

        self.calendly_api_key = os.environ.get("CALENDLY_API_KEY")
        if not self.calendly_api_key:
            raise ValueError("CALENDLY_API_KEY not set in environment")

        self.notifier = Notifier()

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.calendly_api_key}",
                "Content-Type": "application/json",
            }
        )

    def run(self) -> dict:
        """
        check for interested leads and send calendly links
        check calendly api for newly booked events
        returns summary of actions taken
        """
        summary = {
            "calendly_links_sent": 0,
            "calls_booked": 0,
            "errors": 0,
        }

        try:
            self._send_calendly_to_interested(summary)
            self._sync_booked_calls(summary)
        except Exception as e:
            logger.error(f"Booking agent failed: {e}")
            summary["errors"] += 1

        return summary

    def _send_calendly_to_interested(self, summary: dict) -> None:
        """
        find leads marked as interested and send them  a calendly link if one not sent yet
        """
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, place_id, company_name, recipient_email,
                       recipient_name, reply_content
                FROM outreach
                WHERE status = 'interested'
                AND recipient_email IS NOT NULL
                ORDER BY replied_at ASC
                """,
            ).fetchall()

        leads = [dict(row) for row in rows]

        for lead in leads:
            try:
                self._send_calendly_email(lead)
                summary["calendly_links_sent"] += 1
            except Exception as e:
                logger.error(
                    f"Failed to send Calendly link to "
                    f"{lead['recipient_email']}: {e}"
                )
                summary["errors"] += 1

    def _send_calendly_email(self, lead: dict) -> None:
        """
        send a personalised email with calendly booking link.
        """
        executor = EmailExecutor()

        first_name = (
            lead["recipient_name"].split()[0] if lead["recipient_name"] else "there"
        )

        subject = f"Re: SwiftCiv — here's a link to book a call"
        body = (
            f"Hi {first_name},\n\n"
            f"Great to hear from you — here's a link to book a time "
            f"that works for you:\n\n"
            f"{self.calendly_link}\n\n"
            f"The call is 30 minutes. I'll show you SwiftCiv working on "
            f"documents specific to {lead['company_name']}'s work and we can "
            f"discuss what would be most useful for your team.\n\n"
            f"Looking forward to it.\n\n"
            f"Emmanuel\n"
            f"swiftciv.com"
        )

        executor.send(
            outreach_id=lead["id"],
            recipient_email=lead["recipient_email"],
            recipient_name=lead["recipient_name"],
            subject=subject,
            body=body,
            email_type="calendly",
        )

        # update status to booked pending, waiting for them to actually book
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE outreach
                SET status = 'booking_link_sent',
                    updated_at = ?
                WHERE id = ?
                """,
                (now_utc(), lead["id"]),
            )

        logger.info(
            f"Calendly link sent to {lead['recipient_email']} "
            f"at {lead['company_name']}"
        )

    def _sync_booked_calls(self, summary: dict) -> None:
        """
        poll calendly api for newly scheduled events, match them to outreach records by email
        send telegram alert for each new booking
        """
        try:
            events = self._get_recent_calendly_events()

            for event in events:
                try:
                    already_recorded = self._is_already_recorded(
                        event["calendly_event_id"]
                    )
                    if already_recorded:
                        continue

                    outreach = self._find_outreach_by_email(event["invitee_email"])

                    self._record_booking(
                        event=event,
                        place_id=outreach["place_id"] if outreach else None,
                        company_name=(
                            outreach["company_name"]
                            if outreach
                            else event["invitee_email"]
                        ),
                    )

                    if outreach:
                        self._update_outreach_booked(outreach["id"])

                    self._send_booking_alert(
                        company_name=(
                            outreach["company_name"] if outreach else "Unknown Company"
                        ),
                        contact_name=event["invitee_name"],
                        contact_email=event["invitee_email"],
                        scheduled_at=event["scheduled_at"],
                    )

                    summary["calls_booked"] += 1

                except Exception as e:
                    logger.error(
                        f"Error processing Calendly event "
                        f"{event.get('calendly_event_id')}: {e}"
                    )
                    summary["errors"] += 1

        except Exception as e:
            logger.error(f"Failed to sync Calendly events: {e}")
            summary["errors"] += 1

    def _get_recent_calendly_events(self) -> list:
        """
        fetch recently scheduled calendly events
        returns list of event dicts with invitee details
        """
        # get user URI first
        user_response = self.session.get("https://api.calendly.com/users/me")
        user_response.raise_for_status()

        user_uri = user_response.json()["resource"]["uri"]

        # get scheduled events
        response = self.session.get(
            "https://api.calendly.com/scheduled_events",
            params={
                "user": user_uri,
                "status": "active",
                "count": 50,
                "sort": "start_time:desc",
            },
        )
        response.raise_for_status()

        events_data = response.json()

        results = []
        for event in events_data.get("collection", []):
            event_uuid = event["uri"].split("/")[-1]

            # get invitees for this event
            invitees_response = self.session.get(
                f"https://api.calendly.com/scheduled_events/{event_uuid}/invitees"
            )
            if invitees_response.status_code != 200:
                continue

            invitees = invitees_response.json().get("collection", [])
            if not invitees:
                continue

            invitee = invitees[0]
            results.append(
                {
                    "calendly_event_id": event_uuid,
                    "scheduled_at": event["start_time"],
                    "invitee_name": invitee.get("name", ""),
                    "invitee_email": invitee.get("email", ""),
                }
            )

        return results

    def _is_already_recorded(self, calendly_event_id: str) -> bool:
        """
        check if this calendly event is already in the database
        """
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM booked_calls WHERE calendly_event_id = ?",
                (calendly_event_id,),
            ).fetchone()
        return row is not None

    def _find_outreach_by_email(self, email: str) -> Optional[dict]:
        """
        find outreach record matching the calendly invitee email
        """
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, place_id, company_name
                FROM outreach
                WHERE recipient_email = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (email,),
            ).fetchone()
        return dict(row) if row else None

    def _record_booking(
        self, event: dict, place_id: Optional[str], company_name: str
    ) -> None:
        """
        record a confirmed booking in the database
        """
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO booked_calls (
                    place_id, company_name, contact_name,
                    contact_email, scheduled_at,
                    calendly_event_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    place_id or "unknown",
                    company_name,
                    event["invitee_name"],
                    event["invitee_email"],
                    event["scheduled_at"],
                    event["calendly_event_id"],
                    now_utc(),
                ),
            )

    def _update_outreach_booked(self, outreach_id: int) -> None:
        """
        mark outreach record as booked
        """
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE outreach
                SET status = 'booked',
                    booked_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_utc(), now_utc(), outreach_id),
            )

    def _send_booking_alert(
        self,
        company_name: str,
        contact_name: str,
        contact_email: str,
        scheduled_at: str,
    ) -> None:
        """
        send Telegram alert when a call is booked.
        """
        self.notifier.send_alert(
            f"📅 *Call Booked*\n\n"
            f"🏢 Company: {company_name}\n"
            f"👤 Contact: {contact_name}\n"
            f"📧 Email: {contact_email}\n"
            f"🕐 Scheduled: {scheduled_at}\n\n"
            f"Prepare a demo using their specific documents."
        )

        logger.info(f"Call booked — {company_name} — {contact_name} — {scheduled_at}")
