"""
call api routes
manages booked calls from calendly
provides endpoints for viewing upcoming calls and updating outcomes
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from models import get_connection, now_utc

logger = logging.getLogger(__name__)

router = APIRouter()


class CallOutcome(BaseModel):
    outcome: str
    notes: Optional[str] = None


@router.get("/")
async def get_calls():
    """
    get all booked calls sorted by scheduled time
    shows upcoming calls first
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                bc.id,
                bc.company_name,
                bc.contact_name,
                bc.contact_email,
                bc.scheduled_at,
                bc.outcome,
                bc.call_notes,
                bc.created_at,
                cr.state,
                cr.document_type,
                cr.city,
                cr.website,
                cr.company_summary,
                cr.decision_maker_title
            FROM booked_calls bc
            LEFT JOIN company_research cr ON bc.place_id = cr.place_id
            ORDER BY bc.scheduled_at ASC
            """,
        ).fetchall()

    return {"calls": [dict(row) for row in rows]}


@router.get("/upcoming")
async def get_upcoming_calls():
    """
    get calls scheduled in the future
    used by dashboard to show what is coming up
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                bc.id,
                bc.company_name,
                bc.contact_name,
                bc.contact_email,
                bc.scheduled_at,
                bc.outcome,
                cr.state,
                cr.document_type,
                cr.city,
                cr.company_summary
            FROM booked_calls bc
            LEFT JOIN company_research cr ON bc.place_id = cr.place_id
            WHERE bc.scheduled_at > ?
            AND bc.outcome IS NULL
            ORDER BY bc.scheduled_at ASC
            """,
            (now_utc(),),
        ).fetchall()

    return {"upcoming_calls": [dict(row) for row in rows]}


@router.get("/{call_id}")
async def get_call(call_id: int):
    """
    get full details for a single booked call
    includes company research and full outreach history
    """
    with get_connection() as conn:
        call = conn.execute(
            """
            SELECT bc.*, cr.state, cr.document_type,
                   cr.city, cr.website, cr.company_summary,
                   cr.decision_maker_name, cr.decision_maker_title,
                   cr.decision_maker_email
            FROM booked_calls bc
            LEFT JOIN company_research cr ON bc.place_id = cr.place_id
            WHERE bc.id = ?
            """,
            (call_id,),
        ).fetchone()
        if not call:
            raise HTTPException(
                status_code=404,
                detail="Call not found",
            )

        # get outreach history for this company
        outreach_history = conn.execute(
            """
            SELECT o.status, o.sent_at, o.replied_at,
                   o.reply_content, o.email_subject
            FROM outreach o
            WHERE o.place_id = ?
            ORDER BY o.created_at ASC
            """,
            (call["place_id"],),
        ).fetchall()

        # get full email history
        email_history = conn.execute(
            """
            SELECT el.email_type, el.subject,
                   el.sent_at, el.recipient
            FROM email_log el
            JOIN outreach o ON el.outreach_id = o.id
            WHERE o.place_id = ?
            ORDER BY el.sent_at ASC
            """,
            (call["place_id"],),
        ).fetchall()

    return {
        "call": dict(call),
        "outreach_history": [dict(row) for row in outreach_history],
        "email_history": [dict(row) for row in email_history],
    }


@router.patch("/{call_id}/outcome")
async def update_call_outcome(call_id: int, body: CallOutcome):
    """
    update the outcome of a completed call
    Outcomes: closed_swiftciv, closed_bispke, closed_retainer, follow_up_required, not_interested, no_show
    """
    valid_outcomes = {
        "closed_swiftciv",
        "closed_bespoke",
        "closed_retainer",
        "follow_up_required",
        "not_interested",
        "no_show",
    }

    if body.outcome not in valid_outcomes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid outcome. Must be one of: {', '.join(valid_outcomes)}",
        )

    with get_connection() as conn:
        call = conn.execute(
            "SELECT id FROM booked_calls WHERE id = ?",
            (call_id,),
        ).fetchone()

        if not call:
            raise HTTPException(
                status_code=404,
                detail="Call not found",
            )

        conn.execute(
            """
            UPDATE booked_calls
            SET outcome = ?, call_notes = ?
            WHERE id = ?
            """,
            (body.outcome, body.notes, call_id),
        )

        logger.info(f"Call {call_id} outcome updated to {body.outcome}")

        return {
            "call_id": call_id,
            "outcome": body.outcome,
            "notes": body.notes,
        }
