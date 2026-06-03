"""
leads api routes
provides endpoints for viewing and managing leads grouped by state and document type
"""

import logging
from fastapi import APIRouter, Query
from models import get_connection
from fastapi import HTTPException

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def get_leads(
    state: str = Query(None, description="Filter by state"),
    document_type: str = Query(None, description="Filter by document type"),
    status: str = Query(None, description="Filter by outreach status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    get leads with optional filtering by state, document type and status.
    returns paginated results
    """
    with get_connection() as conn:
        conditions = []
        params = []

        if state:
            conditions.append("cr.state = ?")
            params.append(state)

        if document_type:
            conditions.append("cr.document_type = ?")
            params.append(document_type)

        if status:
            conditions.append("o.status = ?")
            params.append(status)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        rows = conn.execute(
            f"""
            SELECT
                cr.place_id,
                cr.company_name,
                cr.city,
                cr.state,
                cr.document_type,
                cr.decision_maker_name,
                cr.decision_maker_title,
                cr.decision_maker_email,
                cr.website,
                cr.company_summary,
                cr.researched_at,
                o.status as outreach_status,
                o.sent_at,
                o.replied_at,
                o.booked_at
            FROM company_research cr
            LEFT JOIN outreach o ON cr.place_id = o.place_id
            {where_clause}
            ORDER BY cr.researched_at DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()

        total = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM company_research cr
            LEFT JOIN outreach o ON cr.place_id = o.place_id
            {where_clause}
            """,
            params,
        ).fetchone()[0]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "leads": [dict(row) for row in rows],
    }


@router.get("/groups")
async def get_lead_groups():
    """
    get leads grouped by state and document type
    shows count of leads in each group and outreach status breakdown
    used by the dashboard overview to show which groups need videos
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                cr.state,
                cr.document_type,
                COUNT(cr.place_id) as total_leads,
                SUM(CASE WHEN o.status = 'video_needed' THEN 1 ELSE 0 END) as video_needed,
                SUM(CASE WHEN o.status = 'queued' THEN 1 ELSE 0 END) as queued,
                SUM(CASE WHEN o.status = 'sent' THEN 1 ELSE 0 END) as sent,
                SUM(CASE WHEN o.status IN ('follow_up_1', 'follow_up_2', 'follow_up_3') THEN 1 ELSE 0 END) as in_followup,
                SUM(CASE WHEN o.status = 'interested' THEN 1 ELSE 0 END) as interested,
                SUM(CASE WHEN o.status = 'booked' THEN 1 ELSE 0 END) as booked,
                SUM(CASE WHEN o.status = 'cold' THEN 1 ELSE 0 END) as cold,
                vl.youtube_url
            FROM company_research cr
            LEFT JOIN outreach o ON cr.place_id = o.place_id
            LEFT JOIN video_library vl
                ON cr.state = vl.state
                AND cr.document_type = vl.document_type
            GROUP BY cr.state, cr.document_type
            ORDER BY cr.state, cr.document_type
            """,
        ).fetchall()

    return {"groups": [dict(row) for row in rows]}


@router.get("/states")
async def get_states():
    """
    get list of all states with leads
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT state, COUNT(*) as lead_count
            FROM company_research
            GROUP BY state
            ORDER BY lead_count DESC
            """,
        ).fetchall()

    return {"states": [dict(row) for row in rows]}


@router.get("/document-types")
async def get_document_types():
    """
    get list of all document types with lead counts
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT document_type, COUNT(*) as lead_count
            FROM company_research
            GROUP BY document_type
            ORDER BY lead_count DESC
            """,
        ).fetchall()

    return {"document_types": [dict(row) for row in rows]}


async def get_lead(place_id: str):
    """
    get full details for a single lead including outreach history
    """
    with get_connection() as conn:
        research = conn.execute(
            "SELECT * FROM company_research WHERE place_id = ?",
            (place_id,),
        ).fetchone()

        if not research:
            raise HTTPException(status_code=404, detail="Lead not found")

        outreach_record = conn.execute(
            "SELECT * FROM outreach WHERE place_id = ?",
            (place_id,),
        ).fetchone()

        email_history = conn.execute(
            """
            SELECT el.*
            FROM email_log el
            JOIN outreach o ON el.outreach_id = o.id
            WHERE o.place_id = ?
            ORDER BY el.sent_at ASC
            """,
            (place_id,),
        ).fetchall()

    return {
        "research": dict(research),
        "outreach": dict(outreach_record) if outreach_record else None,
        "email_history": [dict(row) for row in email_history],
    }
