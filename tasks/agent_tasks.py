"""
agent tasks - research, personalisation of emails, outreach,
monitoring, follow ups and booking pipeline.
each task is independent and will retry if failed.
"""

import logging

from tasks.celery_config import celery_app
from models import get_connection, DocumentType
from agent.researcher import ResearchAgent
from agent.personaliser import PersonalisationAgent
from agent.executor import EmailExecutor
from agent.monitor import EmailMonitor
from agent.follow_up import FollowUpAgent
from agent.booker import BookingAgent

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="tasks.agent_tasks.research_company",
)
def research_company(
    self,
    place_id: str,
    company_name: str,
    website: str,
    address: str,
    city: str,
    state: str,
) -> dict:
    """
    research a single company using grok
    classifies document type and extracts decision maker
    """
    try:
        agent = ResearchAgent()

        result = agent.research(
            place_id=place_id,
            company_name=company_name,
            website=website,
            address=address,
            city=city,
            state=state,
        )

        return {
            "place_id": place_id,
            "company_name": company_name,
            "document_type": result.document_type.value,
            "decision_maker": result.decision_maker_name,
        }

    except Exception as e:
        logger.error(f"Research failed for {company_name}: {e}")
        raise self.retry(exc=e)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="tasks.agent_tasks.personalise_and_queue",
)
def personalise_and_queue(self, place_id: str) -> dict:
    """
    generate personalised email for researched company
    queues it for sending if a video is a available for the state and document type.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                    SELECT place_id, company_name, company_summary,
                        document_type, state, city,
                        decision_maker_name, decision_maker_title,
                        decision_maker_email
                    FROM company_research
                    WHERE place_id = ?
                    """,
                (place_id,),
            ).fetchone()
            if not row:
                logger.warning(f"No research found for place_id {place_id}")
                return {"status": "not_found", "place_id": place_id}

            agent = PersonalisationAgent()

            draft = agent.personalise(
                place_id=row["place_id"],
                company_name=row["company_name"],
                company_summary=row["company_summary"] or "",
                document_type=DocumentType(row["document_type"]),
                state=row["state"],
                city=row["city"],
                decision_maker_name=row["decision_maker_name"],
                decision_maker_title=row["decision_maker_title"],
                decision_maker_email=row["decision_maker_email"],
            )
            if not draft:
                return {"status": "video_needed", "place_id": place_id}

            return {"status": "queued", "place_id": place_id}

    except Exception as e:
        logger.error(f"Personalisation failed for {place_id}: {e}")
        raise self.retry(exc=e)


@celery_app.task(name="tasks.agent_tasks.run_research_pipeline")
def run_research_pipeline() -> dict:
    """
    find all leads pushed to hubspot but not yet researched
    dispatches individual research tasks for each
    runs every 2 hours via celery beat
    """
    try:
        import sqlite3
        from scraper.deduplicator import Deduplicator, DB_PATH as LEADS_DB_PATH

        # ensure leads.db exists
        Deduplicator()

        # query leads.db directly — seen_places lives here not in sales_agent.db
        conn = sqlite3.connect(LEADS_DB_PATH)
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            """
            SELECT sp.place_id, sp.name, sp.website,
                    sp.phone
            FROM seen_places sp
            LEFT JOIN company_research cr
                ON sp.place_id = cr.place_id
            WHERE cr.place_id IS NULL
            AND sp.pushed_to_hubspot = 1
            LIMIT 100
            """,
        ).fetchall()

        leads = [dict(row) for row in rows]
        logger.info(f"Research pipeline — {len(leads)} leads to research")

        for lead in leads:
            research_company.delay(
                place_id=lead["place_id"],
                company_name=lead["name"],
                website=lead["website"] or "",
                address="",
                city="",
                state="",
            )

        return {"dispatched": len(leads)}

    except Exception as e:
        logger.error(f"Research pipeline failed: {e}")
        return {"error": str(e)}


@celery_app.task(name="tasks.agent_tasks.run_outreach_pipeline")
def run_outreach_pipeline() -> dict:
    """
    send all queued emails up to the daily sending limit.
    runs every hour via celery beat
    """
    try:
        executor = EmailExecutor()
        if executor.is_daily_limit_reached():
            logger.info("Daily sending limit reached — skipping outreach pipeline")
            return {"status": "limit_reached"}

        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, company_name, recipient_email,
                       recipient_name, email_subject, email_body
                FROM outreach
                WHERE status = 'queued'
                AND recipient_email IS NOT NULL
                ORDER BY created_at ASC
                LIMIT 50
                """,
            ).fetchall()

        leads = [dict(row) for row in rows]
        sent = 0
        errors = 0

        for lead in leads:
            if executor.is_daily_limit_reached():
                break

            success = executor.send(
                outreach_id=lead["id"],
                recipient_email=lead["recipient_email"],
                recipient_name=lead["recipient_name"],
                subject=lead["email_subject"],
                body=lead["email_body"],
                email_type="initial",
            )
            if success:
                sent += 1
            else:
                errors + 1
        logger.info(f"Outreach pipeline — sent={sent} errors={errors}")
        return {"sent": sent, "errors": errors}

    except Exception as e:
        logger.error(f"Outreach pipeline failed: {e}")
        return {"error": str(e)}


@celery_app.task(name="tasks.agent_tasks.run_monitor_pipeline")
def run_monitor_pipeline() -> dict:
    """
    check gmail inbox for replies to outreach emails
    runs every 30 mins via celery beat
    """
    try:
        monitor = EmailMonitor()

        return monitor.check_replies()

    except Exception as e:
        logger.error(f"Monitor pipeline failed: {e}")
        return {"error": str(e)}


@celery_app.task(name="tasks.agent_tasks.run_followup_pipeline")
def run_followup_pipeline() -> dict:
    """
    send due follow up emails and mark cold leads
    runs every hour via celery beat
    """
    try:
        agent = FollowUpAgent()

        return agent.run()

    except Exception as e:
        logger.error(f"Follow up pipeline failed: {e}")
        return {"error": str(e)}


@celery_app.task(name="tasks.agent_tasks.run_booking_pipeline")
def run_booking_pipeline() -> dict:
    """
    send calendly links to interested leads
    sync newly booked calls from calendly
    run every 15mins via celery beat
    """
    try:
        agent = BookingAgent()

        return agent.run()

    except Exception as e:
        logger.error(f"Booking pipeline failed: {e}")
        return {"error": str(e)}
