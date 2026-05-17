"""
celery tasks for parallel lead generation
runs google places searches concurrently across cities in queries
"""

import logging
import os

from celery import Celery
from dotenv import load_dotenv

from scraper.deduplicator import Deduplicator
from scraper.enricher import Enricher
from scraper.hubspot import HubSpotClient, LeadRecord
from scraper.places import PlacesClient

load_dotenv()

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "lead_scraper",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="tasks.search_and_push",
)
def search_and_push(self, query: str, city: str) -> dict:
    """
    search Google Places for a query in a city.
    deduplicates results and pushes new leads to HubSpot.
    returns a summary of the run.
    """
    results = {
        "query": query,
        "city": city,
        "found": 0,
        "pushed": 0,
        "duplicates": 0,
        "errors": 0,
        "error_details": [],
    }

    try:
        places_client = PlacesClient()
        deduplicator = Deduplicator()
        hubspot_client = HubSpotClient()

        places = places_client.search(query=query, location=city, max_results=20)
        results["found"] = len(places)

        with Enricher() as enricher:
            for place in places:
                try:
                    phone = place.phone_national or place.phone_international or ""

                    # skip if duplicate
                    if deduplicator.is_duplicate(place.place_id, phone):
                        results["duplicates"] += 1
                        continue

                    # mark as seen immediately to prevent race conditions
                    deduplicator.mark_seen(
                        place_id=place.place_id,
                        name=place.name,
                        phone=phone,
                        website=place.website or "",
                    )

                    # enrich with website contact details
                    contact = enricher.enrich(place.website)

                    # parse city and state from address
                    address_parts = place.address.split(",")
                    city_name = (
                        address_parts[-3].strip() if len(address_parts) >= 3 else city
                    )
                    state_name = (
                        address_parts[-2].strip() if len(address_parts) >= 2 else ""
                    )

                    lead = LeadRecord(
                        company_name=place.name,
                        address=place.address,
                        website=place.website,
                        phone=phone,
                        city=city_name,
                        state=state_name,
                        contact_first_name=contact.first_name,
                        contact_last_name=contact.last_name,
                        contact_email=contact.email,
                        contact_title=contact.title,
                        source="google_places",
                        search_query=f"{query} in {city}",
                    )

                    success = hubspot_client.push_lead(lead)

                    if success:
                        deduplicator.mark_pushed(place.place_id)
                        results["pushed"] += 1
                    else:
                        results["errors"] += 1
                        results["error_details"].append(
                            f"{place.name}: HubSpot push returned False"
                        )

                except Exception as e:
                    error_msg = f"{place.name}: {str(e)}"
                    logger.error(f"Error processing place {place.name}: {e}")
                    results["errors"] += 1
                    results["error_details"].append(error_msg)

        logger.info(
            f"Completed: {query} in {city} — "
            f"found={results['found']} pushed={results['pushed']} "
            f"dupes={results['duplicates']} errors={results['errors']}"
        )
        return results

    except Exception as e:
        logger.error(f"Task failed for {query} in {city}: {e}")
        raise self.retry(exc=e)
