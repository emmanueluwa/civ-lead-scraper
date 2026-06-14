"""
no email routes for leads with no email address found
provides endpoints for viewing leads and manually adding emails
when email is added lead is moved to pending for personalisation
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from models import get_connection, now_utc

logger = logging.getLogger(__name__)

router = APIRouter()


class EmailUpdate(BaseModel):
    email: str
    name: Optional[str] = None
    linkedin_url: Optional[str] = None


@router.get("/")
async def get_no_email_leads():
    """
    Get all leads with no_email status.
    Returns company info, website and LinkedIn URL if found.
    """
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                o.id,
                o.place_id,
                o.company_name,
                o.linkedin_url,
                o.status,
                o.updated_at,
                cr.city,
                cr.state,
                cr.website,
                cr.document_type,
                cr.decision_maker_name,
                cr.decision_maker_title
            FROM outreach o
            LEFT JOIN company_research cr ON o.place_id = cr.place_id
            WHERE o.status = 'no_email'
            ORDER BY cr.state, cr.document_type, o.company_name
            """).fetchall()

    return {"leads": [dict(row) for row in rows]}


@router.patch("/{outreach_id}")
async def update_email(outreach_id: int, body: EmailUpdate):
    """
    Add email address to a no_email lead.
    Moves lead to pending status so personaliser picks it up.
    """
    if not body.email or "@" not in body.email:
        raise HTTPException(status_code=400, detail="Invalid email address")

    with get_connection() as conn:
        record = conn.execute(
            "SELECT id FROM outreach WHERE id = ? AND status = 'no_email'",
            (outreach_id,),
        ).fetchone()

        if not record:
            raise HTTPException(
                status_code=404, detail="Lead not found or not in no_email status"
            )

        conn.execute(
            """
            UPDATE outreach
            SET recipient_email = ?,
                recipient_name = ?,
                linkedin_url = ?,
                status = 'pending',
                updated_at = ?
            WHERE id = ?
            """,
            (
                body.email,
                body.name,
                body.linkedin_url,
                now_utc(),
                outreach_id,
            ),
        )

    logger.info(
        f"Email added for outreach {outreach_id} — " f"{body.email} — moved to pending"
    )

    return {"outreach_id": outreach_id, "email": body.email, "status": "pending"}


@router.patch("/{outreach_id}/linkedin")
async def update_linkedin(outreach_id: int, body: dict):
    """
    Update LinkedIn URL for a no_email lead.
    Called by the email finder script after collecting URLs.
    """
    linkedin_url = body.get("linkedin_url")
    if not linkedin_url:
        raise HTTPException(status_code=400, detail="linkedin_url required")

    with get_connection() as conn:
        record = conn.execute(
            "SELECT id FROM outreach WHERE id = ?",
            (outreach_id,),
        ).fetchone()

        if not record:
            raise HTTPException(status_code=404, detail="Lead not found")

        conn.execute(
            """
            UPDATE outreach
            SET linkedin_url = ?, updated_at = ?
            WHERE id = ?
            """,
            (linkedin_url, now_utc(), outreach_id),
        )

    return {"outreach_id": outreach_id, "linkedin_url": linkedin_url}
