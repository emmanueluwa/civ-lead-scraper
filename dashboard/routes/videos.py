"""
video api routes
manages the video library - mapping youtube urls to state and document combination
this is how the agent knows which video to send to which leads
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl
from typing import Optional

from models import get_connection, now_utc
from tasks.notification_tasks import _get_suggested_questions

logger = logging.getLogger(__name__)

router = APIRouter()


class VideoUpdate(BaseModel):
    youtube_url: str


@router.get("/")
async def get_videos():
    """
    get all video library entries
    shows which state and document type groups have videos and which are still needed
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                vl.id,
                vl.state,
                vl.document_type,
                vl.youtube_url,
                vl.updated_at,
                COUNT(o.id) as leads_waiting
            FROM video_library vl
            LEFT JOIN company_research cr
                ON cr.state = vl.state
                AND cr.document_type = vl.document_type
            LEFT JOIN outreach o
                ON o.place_id = cr.place_id
                AND o.status = 'video_needed'
            GROUP BY vl.id, vl.state, vl.document_type,
                     vl.youtube_url, vl.updated_at
            ORDER BY leads_waiting DESC, vl.state, vl.document_type
            """,
        ).fetchall()

    return {
        "videos": [
            {
                **dict(row),
                "has_video": row["youtube_url"] is not None,
                "suggested_questions": (
                    _get_suggested_questions(row["document_type"])
                    if row["youtube_url"] is None
                    else []
                ),
            }
            for row in rows
        ]
    }


@router.get("/needed")
async def get_videos_needed():
    """
    get only the groups that still need a video recorded
    includes suggested questions for each group
    sorted by number of leads wiating so highest priority first
    """
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

    return {
        "videos_needed": [
            {
                "state": row["state"],
                "document_type": row["document_type"],
                "leads_waiting": row["leads_waiting"],
                "suggested_questions": _get_suggested_questions(row["document_type"]),
            }
            for row in rows
        ]
    }


@router.put("/{state}/{document_type}")
async def update_video(state: str, document_type: str, body: VideoUpdate):
    """
    add or update a youtube video url for a state and document type group
    once a video is added all leads in that group are moved from video_needed to queued
    each lead is then ready for the personaliser to draft emails
    """
    with get_connection() as conn:
        # upsert video URL
        conn.execute(
            """
            INSERT INTO video_library
                (state, document_type, youtube_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(state, document_type) DO UPDATE SET
                youtube_url = excluded.youtube_url,
                updated_at = excluded.updated_at
            """,
            (state, document_type, body.youtube_url, now_utc(), now_utc()),
        )

        # move all video_needed leads for this group to pending
        # so the personaliser picks them up and drafts emails
        result = conn.execute(
            """
            UPDATE outreach
            SET status = 'pending',
                updated_at = ?
            WHERE status = 'video_needed'
            AND place_id IN (
                SELECT place_id FROM company_research
                WHERE state = ? AND document_type = ?
            )
            """,
            (now_utc(), state, document_type),
        )
        leads_unblocked = result.rowcount

    logger.info(
        f"Video added for {state} + {document_type} — "
        f"{leads_unblocked} leads unblocked"
    )

    return {
        "state": state,
        "document_type": document_type,
        "youtube_url": body.youtube_url,
        "leads_unblocked": leads_unblocked,
    }


@router.delete("/{state}/{document_type}")
async def delete_video(state: str, document_type: str):
    """
    remove a video url from the library
    leads in this group revert to video_needed status
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM video_library
            WHERE state = ? AND document_type = ?
            """,
            (state, document_type),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Video not found")

        conn.execute(
            """
            UPDATE video_library
            SET youtube_url = NULL, updated_at = ?
            WHERE state = ? AND document_type = ?
            """,
            (now_utc(), state, document_type),
        )

        # revert queued leads back to video_needed
        conn.execute(
            """
            UPDATE outreach
            SET status = 'video_needed', updated_at = ?
            WHERE status IN ('queued', 'pending')
            AND place_id IN (
                SELECT place_id FROM company_research
                WHERE state = ? AND document_type = ?
            )
            """,
            (now_utc(), state, document_type),
        )

    return {"message": f"Video removed for {state} + {document_type}"}
