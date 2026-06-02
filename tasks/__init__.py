"""
tasks package - exports celery_app and imports all task modules
"""

from tasks.celery_config import celery_app
from tasks import scraper_tasks, agent_tasks, notification_tasks

__all__ = ["celery_app"]
