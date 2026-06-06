"""
runs google places searches and pushes leads to hubspot
"""

import logging

from celery import Celery
from dotenv import load_dotenv

from tasks.celery_config import celery_app
from scraper.deduplicator import Deduplicator
from scraper.enricher import Enricher
from scraper.hubspot import HubSpotClient, LeadRecord
from scraper.places import PlacesClient

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="tasks.scraper_tasks.search_and_push",
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
                        city=city_name,
                        state=state_name,
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
