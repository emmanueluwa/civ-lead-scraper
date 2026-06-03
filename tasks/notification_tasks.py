"""
notification tasks - video recording alerts and daily pipeline reports
updates on what needs to be recorded and how the pipeline is performing
"""

import logging

from tasks.celery_config import celery_app
from models import get_connection, now_utc
from scraper.notifier import Notifier

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.notification_tasks.run_video_notification")
def run_video_notification() -> dict:
    """
    check for state and document type groups that need a video recorded
    send tele notification with the groups and suggested questions
    runs once daily via celery beat
    """
    try:
        notifier = Notifier()

        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    vl.state,
                    vl.document_type,
                    COUNT(o.id) as leads_waiting
                FROM video_library vl
                LEFT JOIN company_research cr
                    ON cr.state = vl.state
                    AND cr.document_type = vl.document_type
                LEFT JOIN outreach o
                    ON o.place_id = cr.place_id
                    AND o.status = 'video_needed'
                WHERE vl.youtube_url IS NULL
                GROUP BY vl.state, vl.document_type
                HAVING leads_waiting > 0
                ORDER BY leads_waiting DESC
                """,
            ).fetchall()

        if not rows:
            logger.info("No videos needed — all groups covered")
            return {"videos_needed": 0}

        # building message grouped by state
        message = "📹 *Videos Needed*\n\n"
        message += "Record a SwiftCiv demo for each group below.\n"
        message += "Upload to YouTube and paste the link in the dashboard.\n\n"

        for row in rows:
            questions = _get_suggested_questions(row["document_type"])
            message += (
                f"*{row['state']} — {row['document_type'].replace('_', ' ').title()}*\n"
                f"{row['leads_waiting']} leads waiting\n"
                f"Suggested questions:\n"
            )
            for q in questions:
                message += f"  • {q}\n"
            message += "\n"

        notifier._send(message)

        logger.info(f"Video notification sent — {len(rows)} groups need recording")
        return {"videos_needed": len(rows)}

    except Exception as e:
        logger.error(f"Video notification failed: {e}")
        return {"error": str(e)}


@celery_app.task(name="tasks.notification_tasks.run_daily_report")
def run_daily_report() -> dict:
    """
    send a daily summary of the sales agent pipeline performance
    covers outreach sent, replies, interested leads, and booked calls
    """
    try:
        notifier = Notifier()

        with get_connection() as conn:
            # outreach stats
            total_sent = conn.execute(
                "SELECT COUNT(*) FROM outreach WHERE status != 'pending'"
            ).fetchone()[0]

            sent_today = conn.execute(
                """
                SELECT COUNT(*) FROM email_log
                WHERE sent_at LIKE ? AND email_type = 'initial'
                """,
                (f"{now_utc()[:10]}%",),
            ).fetchone()[0]

            total_replies = conn.execute(
                "SELECT COUNT(*) FROM outreach WHERE replied_at IS NOT NULL"
            ).fetchone()[0]

            total_interested = conn.execute(
                "SELECT COUNT(*) FROM outreach WHERE status = 'interested'"
            ).fetchone()[0]

            total_booked = conn.execute("SELECT COUNT(*) FROM booked_calls").fetchone()[
                0
            ]

            total_cold = conn.execute(
                "SELECT COUNT(*) FROM outreach WHERE status = 'cold'"
            ).fetchone()[0]

            videos_needed = conn.execute("""
                SELECT COUNT(*) FROM video_library
                WHERE youtube_url IS NULL
                """).fetchone()[0]

        # calculate reply rate
        reply_rate = (
            round((total_replies / total_sent) * 100, 1) if total_sent > 0 else 0
        )

        message = (
            f"*Sales Agent Daily Report*\n"
            f"{now_utc()[:10]}\n\n"
            f"*Outreach*\n"
            f"Sent today: {sent_today}\n"
            f"Total sent: {total_sent}\n"
            f"Total replies: {total_replies}\n"
            f"Reply rate: {reply_rate}%\n\n"
            f"*Pipeline*\n"
            f"Interested: {total_interested}\n"
            f"Calls booked: {total_booked}\n"
            f"Cold leads: {total_cold}\n\n"
            f"*Videos*\n"
            f"Groups needing video: {videos_needed}\n"
        )

        notifier._send(message)

        logger.info("Daily report sent")
        return {
            "sent_today": sent_today,
            "total_sent": total_sent,
            "total_replies": total_replies,
            "total_booked": total_booked,
        }

    except Exception as e:
        logger.error(f"Daily report failed: {e}")
        return {"error": str(e)}


def _get_suggested_questions(document_type: str) -> list[str]:
    """
    Return 5 suggested demo questions for a given document type.
    Used in the video recording notification.
    """
    questions = {
        "stormwater": [
            "What is the minimum pipe cover for a 24 inch RCP?",
            "What design storm is required for a local road not in a floodplain?",
            "What is the minimum clearance from roadbase to seasonal high water?",
            "What are the detention pond setback requirements?",
            "How is the water quality volume calculated for a commercial site?",
        ],
        "land_development": [
            "What is the minimum lot size for a single family residential development?",
            "What are the setback requirements for a commercial property?",
            "What is the maximum impervious surface ratio allowed?",
            "What are the sidewalk requirements for a new subdivision?",
            "What is the minimum right of way width for a local road?",
        ],
        "structural": [
            "What is the minimum concrete compressive strength for footings?",
            "What are the wind load requirements for this region?",
            "What is the minimum reinforcement cover for below grade concrete?",
            "What are the seismic design requirements for this zone?",
            "What is the maximum allowable deflection for a floor beam?",
        ],
        "transportation": [
            "What is the minimum sight distance for a stop controlled intersection?",
            "What are the lane width requirements for a collector road?",
            "What is the maximum grade allowed for a local street?",
            "What are the bicycle lane width requirements?",
            "What is the minimum turning radius for a standard intersection?",
        ],
        "municipal": [
            "What is the minimum water main size for a residential development?",
            "What are the fire hydrant spacing requirements?",
            "What is the minimum sewer pipe slope for an 8 inch main?",
            "What are the requirements for a lift station wet well?",
            "What is the minimum pressure requirement for the water distribution system?",
        ],
        "geotechnical": [
            "What is the minimum number of soil borings required for a site investigation?",
            "What are the minimum compaction requirements for fill material?",
            "What is the minimum factor of safety required for slope stability?",
            "What are the requirements for dewatering during excavation?",
            "What is the minimum embedment depth for a retaining wall?",
        ],
        "unknown": [
            "What are the general design requirements for this project type?",
            "What permits are required for this type of development?",
            "What are the submission requirements for a development application?",
            "What are the inspection requirements during construction?",
            "What are the as-built documentation requirements?",
        ],
    }

    return questions.get(document_type, questions["unknown"])
