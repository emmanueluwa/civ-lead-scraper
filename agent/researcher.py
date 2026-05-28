"""
Research agent — classifies civil engineering companies by state and document type.
Uses Grok to analyse company website and determine what type of civil engineering
work they do and what regulatory documents they would use daily.
"""

import logging
import os
import json
from dataclasses import dataclass
from typing import Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from models import DocumentType, get_connection, now_utc

logger = logging.getLogger(__name__)

GROK_API_URL = "https://api.x.ai/v1/chat/completions"
GROK_MODEL = "grok-3"


@dataclass
class ResearchResult:
    place_id: str
    company_name: str
    document_type: DocumentType
    decision_maker_name: Optional[str]
    decision_maker_title: Optional[str]
    decision_maker_email: Optional[str]
    company_summary: str


class ResearchAgent:
    def __init__(self):
        self.api_key = os.environ.get("GROK_API_KEY")
        if not self.api_key:
            raise ValueError("GROK_API_KEY not set in environment")

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        )

    @retry(
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
    )
    def research(
        self,
        place_id: str,
        company_name: str,
        website: Optional[str],
        address: str,
        city: str,
        state: str,
    ) -> ResearchResult:
        """
        research civil engineering company using grok
        determines the type of documents the company would need swiftciv for and decision maker if possible
        returns ResearchResult with classification and summary
        """

        prompt = self.build_prompt(
            company_name=company_name,
            website=website,
            address=address,
            city=city,
            state=state,
        )

        response = self._call_grok(prompt)
        result = self._parse_response(response, place_id, company_name)

        self._save_to_db(
            result=result, website=website, address=address, city=city, state=state
        )

        return result

    def build_prompt(
        self,
        company_name: str,
        website: Optional[str],
        address: str,
        city: str,
        state: str,
    ) -> str:
        return f"""
You are a research analyst identifying civil engineering companies for a targeted outreach campaign.

Company: {company_name}
Location: {city}, {state}
Address: {address}
Website: {website or "Not available"}

Your task: Analyse this civil engineering company and return a JSON object with the following fields:

1. document_type: The PRIMARY type of regulatory documents this company works with daily.
   Choose exactly one from: stormwater, land_development, structural, transportation, municipal, geotechnical, unknown
   
   Guidelines:
   - stormwater: drainage design, flood control, water quality, detention ponds
   - land_development: site planning, subdivision design, grading, utilities
   - structural: buildings, bridges, foundations, retaining walls
   - transportation: roads, highways, traffic, intersections
   - municipal: public works, water/sewer systems, parks infrastructure
   - geotechnical: soil testing, slope stability, earthworks
   - unknown: cannot determine from available information

2. decision_maker_name: Full name of the principal engineer, director, founder, or president if determinable. null if unknown.

3. decision_maker_title: Their job title. null if unknown.

4. company_summary: One sentence describing what this company does and who their typical clients are. Be specific.

Return ONLY a valid JSON object. No preamble, no explanation, no markdown.

Example:
{{"document_type": "stormwater", "decision_maker_name": "John Smith", "decision_maker_title": "Principal Engineer", "company_summary": "Davidson Engineering provides stormwater and drainage design services to residential developers and municipalities across Manatee County, Florida."}}
"""

    @retry(
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
    )
    def _call_grok(self, prompt: str) -> dict:
        """call grok api and return parsed response"""
        response = self.session.post(
            GROK_API_URL,
            json={
                "model": GROK_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a precise research analyst. "
                            "You always return valid JSON and nothing else."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "temperature": 0.1,
                "max_tokens": 500,
            },
        )
        response.raise_for_status()
        return response.json()

    def _parse_response(
        self, response: dict, place_id: str, company_name: str
    ) -> ResearchResult:
        """parse grok response into ResearchResult"""
        try:
            content = response["choices"][0]["message"]["content"].strip()

            # strip markdown code fences if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]

            data = json.loads(content)

            # validate and normalise document type
            doc_type_str = data.get("document_type", "unknown").lower()
            try:
                document_type = DocumentType(doc_type_str)
            except ValueError:
                document_type = DocumentType.UNKNOWN

            return ResearchResult(
                place_id=place_id,
                company_name=company_name,
                document_type=document_type,
                decision_maker_name=data.get("decision_maker_name"),
                decision_maker_title=data.get("decision_maker_title"),
                decision_maker_email=data.get("decision_maker_email"),
                company_summary=data.get("company_summary", ""),
            )

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning(
                f"Failed to parse Grok response for {company_name}: {e}. "
                f"Defaulting to unknown."
            )

            return ResearchResult(
                place_id=place_id,
                company_name=company_name,
                document_type=DocumentType.UNKNOWN,
                decision_maker_name=None,
                decision_maker_title=None,
                decision_maker_email=None,
                company_summary="",
            )

    def _save_to_db(
        self,
        result: ResearchResult,
        website: Optional[str],
        address: str,
        city: str,
        state: str,
    ) -> None:
        """persist research result to database"""
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO company_research (
                    place_id, company_name, website, address,
                    city, state, document_type,
                    decision_maker_name, decision_maker_title,
                    decision_maker_email, company_summary,
                    researched_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(place_id) DO UPDATE SET
                    document_type = excluded.document_type,
                    decision_maker_name = excluded.decision_maker_name,
                    decision_maker_title = excluded.decision_maker_title,
                    decision_maker_email = excluded.decision_maker_email,
                    company_summary = excluded.company_summary,
                    researched_at = excluded.researched_at
                """,
                (
                    result.place_id,
                    result.company_name,
                    website,
                    address,
                    city,
                    state,
                    result.document_type.value,
                    result.decision_maker_name,
                    result.decision_maker_title,
                    result.decision_maker_email,
                    result.company_summary,
                    now_utc(),
                    now_utc(),
                ),
            )

        logger.info(
            f"Researched: {result.company_name} → "
            f"{result.document_type.value} | "
            f"Decision maker: {result.decision_maker_name or 'unknown'}"
        )
