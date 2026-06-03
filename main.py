"""
lead generation pipeline entry point
daily report to telegram
"""

import logging
import os
import time
from datetime import datetime, timezone

from celery import group
from dotenv import load_dotenv

from cities import CITIES, SEARCH_QUERIES
from scraper.deduplicator import Deduplicator
from scraper.notifier import Notifier
from tasks.scraper_tasks import search_and_push
from models import init_db

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/app/data/scraper.log"),
    ],
)

logger = logging.getLogger(__name__)


# number of cities to search per run
# 20 cities × 10 queries × 20 results = up to 4,000 raw results
# estimation 300-500 clean leads
CITIES_PER_RUN = 3


def get_cities_for_today() -> list[str]:
    """
    rotate throught cities daily so every city get searched over time.
    """
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
    start_index = (day_of_year * CITIES_PER_RUN) % len(CITIES)
    end_index = start_index + CITIES_PER_RUN

    if end_index <= len(CITIES):
        return CITIES[start_index:end_index]
    else:
        # wrap around
        return CITIES[start_index:] + CITIES[: end_index - len(CITIES)]


def run():
    """
    dispatches parallel celery tasks for all city/query combinations
    sends report to telegram after completion
    """
    start_time = time.time()

    init_db()

    notifier = Notifier()
    deduplicator = Deduplicator()

    cities_today = get_cities_for_today()
    logger.info(
        f"Starting lead generation — "
        f"{len(cities_today)} cities × {len(SEARCH_QUERIES)} queries = "
        f"{len(cities_today) * len(SEARCH_QUERIES)} tasks"
    )

    # build task group, all tasks run in parallel
    tasks = group(
        search_and_push.s(query=query, city=city)
        for city in cities_today
        for query in SEARCH_QUERIES
    )

    try:
        # dispatch all tasks
        result = tasks.apply_async()

        results = result.get(timeout=3600, propagate=False)

        # aggregate results
        total_found = 0
        total_pushed = 0
        total_duplicates = 0
        total_errors = 0
        all_error_details = []

        for r in results:
            if isinstance(r, dict):
                total_found += r.get("found", 0)
                total_pushed += r.get("pushed", 0)
                total_duplicates += r.get("duplicates", 0)
                total_errors += r.get("errors", 0)
                all_error_details.extend(r.get("error_details", []))

        duration = time.time() - start_time
        db_stats = deduplicator.get_stats()

        logger.info(
            f"Pipeline complete — "
            f"found={total_found} pushed={total_pushed} "
            f"dupes={total_duplicates} errors={total_errors} "
            f"duration={round(duration, 1)}s"
        )

        notifier.send_daily_report(
            leads_found=total_found,
            leads_pushed=total_pushed,
            duplicates_skipped=total_duplicates,
            errors=total_errors,
            cities_searched=len(cities_today),
            queries_run=len(cities_today) * len(SEARCH_QUERIES),
            duration_seconds=duration,
            db_stats=db_stats,
            error_details=all_error_details[:10],
        )

    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"pipeline failed: {e}")
        notifier.send_alert(
            f"Lead scraper pipeline failed after {round(duration, 1)}s\n"
            f"Error: {str(e)}"
        )
        raise


if __name__ == "__main__":
    run()
