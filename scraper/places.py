"""
Google places API used to discover civil engineering firms
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

PLACES_API_URL = "https://places.googleapis.com/v1/places:searchText"

FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "places.userRatingCount",
        "places.businessStatus",
        "places.types",
    ]
)


@dataclass
class Place:
    place_id: str
    name: str
    address: str
    phone_national: Optional[str]
    phone_international: Optional[str]
    website: Optional[str]
    rating: Optional[float]
    rating_count: Optional[int]
    business_status: str


class PlacesClient:
    def __init__(self):
        self.api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_PLACES_API_KEY not set in environment")

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": FIELD_MASK,
            }
        )

    @retry(
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
    )
    def search(self, query: str, location: str, max_results: int = 20) -> list[Place]:
        text_query = f"{query} in {location}"

        payload = {
            "textQuery": text_query,
            "maxResultCount": min(max_results, 20),  # API max is 20
            "languageCode": "en",
        }

        try:
            response = self.session.post(PLACES_API_URL, json=payload)
            response.raise_for_status()
            data = response.json()

            places = []
            for item in data.get("places", []):
                # skip closed businesses
                if item.get("businessStatus") == "CLOSED_PERMANENTLY":
                    continue

                place = Place(
                    place_id=item.get("id", ""),
                    name=item.get("displayName", {}).get("text", ""),
                    address=item.get("formattedAddress", ""),
                    phone_national=item.get("nationalPhoneNumber"),
                    phone_international=item.get("internationalPhoneNumber"),
                    website=item.get("websiteUri"),
                    rating=item.get("rating"),
                    rating_count=item.get("userRatingCount"),
                    business_status=item.get("businessStatus", ""),
                )

                # only include places with a phone number
                if place.phone_national or place.phone_international:
                    places.append(place)

            logger.info(f"Found {len(places)} results for '{text_query}'")

            return places

        except requests.exceptions.HTTPError as e:
            logger.error(f"Places API HTTP error for '{text_query}': {e}")
            raise
        except Exception as e:
            logger.error(f"Places API error for '{text_query}': {e}")
            raise
