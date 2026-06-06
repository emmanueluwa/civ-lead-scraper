"""
dashboard for sales agent pipeline
provides visibility into leads, outreach status, video library and booked calls
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from models import init_db
from dashboard.routes import leads, videos, outreach, calls
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

PROXY_TRUSTED_HOSTS = os.environ.get("PROXY_TRUSTED_HOSTS")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    initialise database on startup.
    """
    logger.info("Dashboard starting up — initialising database")
    init_db()
    logger.info("Dashboard ready")
    yield
    logger.info("Dashboard shutting down")


app = FastAPI(
    title="SwiftCiv Sales Agent Dashboard",
    description="Monitor and manage the automated sales pipeline",
    version="1.0.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=PROXY_TRUSTED_HOSTS)


# mount routes
app.include_router(leads.router, prefix="/api/leads", tags=["leads"])
app.include_router(videos.router, prefix="/api/videos", tags=["videos"])
app.include_router(outreach.router, prefix="/api/outreach", tags=["outreach"])
app.include_router(calls.router, prefix="/api/calls", tags=["calls"])


@app.get("/api/health")
async def health():
    """
    health check endpoint
    """
    return {"status": "ok"}


@app.get("/api/stats")
async def stats():
    """Overall pipeline statistics."""
    from models import get_connection, now_utc

    with get_connection() as conn:
        total_leads = conn.execute("SELECT COUNT(*) FROM company_research").fetchone()[
            0
        ]

        total_queued = conn.execute(
            "SELECT COUNT(*) FROM outreach WHERE status = 'queued'"
        ).fetchone()[0]

        total_sent = conn.execute(
            "SELECT COUNT(*) FROM outreach WHERE status != 'pending'"
        ).fetchone()[0]

        total_replied = conn.execute(
            "SELECT COUNT(*) FROM outreach WHERE replied_at IS NOT NULL"
        ).fetchone()[0]

        total_interested = conn.execute(
            "SELECT COUNT(*) FROM outreach WHERE status = 'interested'"
        ).fetchone()[0]

        total_booked = conn.execute("SELECT COUNT(*) FROM booked_calls").fetchone()[0]

        videos_needed = conn.execute(
            "SELECT COUNT(*) FROM video_library WHERE youtube_url IS NULL"
        ).fetchone()[0]

        sent_today = conn.execute(
            """
            SELECT COUNT(*) FROM email_log
            WHERE sent_at LIKE ? AND email_type = 'initial'
            """,
            (f"{now_utc()[:10]}%",),
        ).fetchone()[0]

    reply_rate = round((total_replied / total_sent) * 100, 1) if total_sent > 0 else 0

    return {
        "total_leads": total_leads,
        "total_queued": total_queued,
        "total_sent": total_sent,
        "sent_today": sent_today,
        "total_replied": total_replied,
        "reply_rate": reply_rate,
        "total_interested": total_interested,
        "total_booked": total_booked,
        "videos_needed": videos_needed,
    }
