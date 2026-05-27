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
        pass
