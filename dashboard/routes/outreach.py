"""
outreach api routes
provides visibility into the email outreach pipeline
shows sending status, replies, and follow up state per lead
"""

import logging
from fastapi import APIRouter, Query, HTTPException
from models import get_connection, now_utc

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def get_outreach(
    status: str = Query(None, description="Filter by outreach status"),
    state: str = Query(None, description="Filter by state"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    get outreach records with optional filtering
    return paginated results with company and email details
    """
    with get_connection() as conn:
        conditions = []
        params = []

        if status:
            conditions.append("o.status = ?")
            params.append(status)

        if state:
            conditions.append("cr.state = ?")
            params.append(state)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        rows = conn.execute(
            f"""
            SELECT
                o.id,
                o.place_id,
                o.company_name,
                o.recipient_email,
                o.recipient_name,
                o.status,
                o.email_subject,
                o.youtube_url,
                o.sent_at,
                o.follow_up_1_at,
                o.follow_up_2_at,
                o.follow_up_3_at,
                o.replied_at,
                o.booked_at,
                o.cold_at,
                o.created_at,
                cr.state,
                cr.document_type,
                cr.city
            FROM outreach o
            LEFT JOIN company_research cr ON o.place_id = cr.place_id
            {where_clause}
            ORDER BY o.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()

        total = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM outreach o
            LEFT JOIN company_research cr ON o.place_id = cr.place_id
            {where_clause}
            """,
            params,
        ).fetchone()[0]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "outreach": [dict(row) for row in rows],
    }


@router.get("/summary")
async def get_outreach_summary():
    """
    get a summary of outreach pipeline status counts
    used by dashboard overview cards
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) as count
            FROM outreach
            GROUP BY status
            ORDER BY count DESC
            """,
        ).fetchall()

        sent_today = conn.execute(
            """
            SELECT COUNT(*) FROM email_log
            WHERE sent_at LIKE ? AND email_type = 'initial'
            """,
            (f"{now_utc()[:10]}%",),
        ).fetchone()[0]

        total_emails_sent = conn.execute("SELECT COUNT(*) FROM email_log").fetchone()[0]

    status_counts = {row["status"]: row["count"] for row in rows}

    return {
        "status_counts": status_counts,
        "sent_today": sent_today,
        "total_emails_sent": total_emails_sent,
    }


@router.get("/replies")
async def get_replies(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    get all outreach records that have received a reply
    sorted by most recent reply first
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                o.id,
                o.company_name,
                o.recipient_email,
                o.recipient_name,
                o.status,
                o.replied_at,
                o.reply_content,
                cr.state,
                cr.document_type,
                cr.city
            FROM outreach o
            LEFT JOIN company_research cr ON o.place_id = cr.place_id
            WHERE o.replied_at IS NOT NULL
            ORDER BY o.replied_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

        total = conn.execute(
            "SELECT COUNT(*) FROM outreach WHERE replied_at IS NOT NULL"
        ).fetchone()[0]

    return {
        "total": total,
        "replies": [dict(row) for row in rows],
    }


@router.get("/{outreach_id}")
async def get_outreach_detail(outreach_id: int):
    """
    get full details for a single outreach record
    includes complete email history
    """
    with get_connection() as conn:
        record = conn.execute(
            """
            SELECT o.*, cr.state, cr.document_type,
                   cr.city, cr.company_summary,
                   cr.decision_maker_title
            FROM outreach o
            LEFT JOIN company_research cr ON o.place_id = cr.place_id
            WHERE o.id = ?
            """,
            (outreach_id,),
        ).fetchone()

        if not record:
            raise HTTPException(
                status_code=404,
                detail="Outreach record not found",
            )

        email_history = conn.execute(
            """
            SELECT id, email_type, recipient, subject,
                   gmail_message_id, sent_at
            FROM email_log
            WHERE outreach_id = ?
            ORDER BY sent_at ASC
            """,
            (outreach_id,),
        ).fetchall()

    return {
        "outreach": dict(record),
        "email_history": [dict(row) for row in email_history],
    }


@router.patch("/{outreach_id}/unsubscribe")
async def unsubscribe(outreach_id: int):
    """
    manually mark a lead as unsubscribed
    stops all future outreach to this email address
    """
    with get_connection() as conn:
        record = conn.execute(
            "SELECT id FROM outreach WHERE id = ?",
            (outreach_id,),
        ).fetchone()

        if not record:
            raise HTTPException(
                status_code=404,
                detail="Outreach record not found",
            )

        conn.execute(
            """
            UPDATE outreach
            SET status = 'unsubscribed', updated_at = ?
            WHERE id = ?
            """,
            (now_utc(), outreach_id),
        )

    logger.info(f"Outreach {outreach_id} manually marked as unsubscribed")
    return {"message": "Marked as unsubscribed"}
