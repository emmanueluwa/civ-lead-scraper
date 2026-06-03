"""
celery application config and beat schedule
"""

import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "lead_scraper",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "tasks.scraper_tasks",
        "tasks.agent_tasks",
        "tasks.notification_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "tasks.scraper_tasks.*": {"queue": "scraper"},
        "tasks.agent_tasks.*": {"queue": "agent"},
        "tasks.notification_tasks.*": {"queue": "agent"},
    },
    beat_schedule={
        # research and classify new leads — every 2 hours
        "research-and-personalise": {
            "task": "tasks.agent_tasks.run_research_pipeline",
            "schedule": 7200,
        },
        # send queued outreach emails — every hour
        "send-queued-emails": {
            "task": "tasks.agent_tasks.run_outreach_pipeline",
            "schedule": 3600,
        },
        # check Gmail for replies — every 30 minutes
        "monitor-replies": {
            "task": "tasks.agent_tasks.run_monitor_pipeline",
            "schedule": 1800,
        },
        # send due follow up emails — every hour
        "send-follow-ups": {
            "task": "tasks.agent_tasks.run_followup_pipeline",
            "schedule": 3600,
        },
        # sync Calendly bookings — every 15 minutes
        "sync-bookings": {
            "task": "tasks.agent_tasks.run_booking_pipeline",
            "schedule": 900,
        },
        # notify about videos needed — once daily at 8am UTC
        "video-notifications": {
            "task": "tasks.notification_tasks.run_video_notification",
            "schedule": 86400,
        },
        # daily pipeline performance report — every day at 8pm UTC
        "daily-report": {
            "task": "tasks.notification_tasks.run_daily_report",
            "schedule": 86400,
        },
    },
)
