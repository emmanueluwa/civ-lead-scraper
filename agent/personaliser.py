"""
personalisation agent - writes unique cold emails per company using Grok.
every email references the company's specific work, location, and document type
emebeds the relevant youtube video for their state and document type
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
class EmailDraft:
    subject: str
    body: str
    recipient_name: Optional[str]
    recipient_email: Optional[str]
    youtube_url: Optional[str]


class PersonalisationAgent:
    def __init__(self):
        self.api_key = os.environ.get("GROK_API_KEY")
        if not self.api_key:
            raise ValueError("GROK_API_KEY not set in environment")

        self.calendly_link = os.environ.get("CALENDLY_LINK")
        if not self.calendly_link:
            raise ValueError("CALENDLY_LINK not set in environment")

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        )

    def personalise(
        self,
        place_id: str,
        company_name: str,
        company_summary: str,
        document_type: DocumentType,
        state: str,
        city: str,
        decision_maker_name: Optional[str],
        decision_maker_title: Optional[str],
        decision_maker_email: Optional[str],
    ) -> Optional[EmailDraft]:
        """
        generate a personalised cold email for a company
        looks up the youtube video for their state + document type
        returns None if no video is available for this group yet
        """
        youtube_url = self._get_video_url(state, document_type)

        if not youtube_url:
            logger.info(
                f"No video available for {state} + {document_type.value} — "
                f"skipping {company_name}"
            )
            self._mark_video_needed(place_id, company_name, state, document_type)

            return None

        prompt = self._build_prompt(
            company_name=company_name,
            company_summary=company_summary,
            document_type=document_type,
            state=state,
            city=city,
            decision_maker_name=decision_maker_name,
            decision_maker_title=decision_maker_title,
            youtube_url=youtube_url,
        )

        response = self._call_grok(prompt)

        draft = self._parse_response(
            response=response,
            decision_maker_name=decision_maker_name,
            decision_maker_email=decision_maker_email,
            youtube_url=youtube_url,
        )

        self._save_draft(place_id, company_name, draft)

        return draft

    def _get_video_url(self, state: str, document_type: DocumentType) -> Optional[str]:
        """
        look up youtube video url for this state + document type combo
        """
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT youtube_url FROM video_library
                WHERE state = ? AND document_type = ?
                AND youtube_url IS NOT NULL
                """,
                (state, document_type.value),
            ).fetchone()

        return row["youtube_url"] if row else None

    def _mark_video_needed(
        self, place_id: str, company_name: str, state: str, document_type: DocumentType
    ) -> None:
        """
        mark state and document type as needing a video
        ensures video library has row for this group so the dashboard can see it as pending.
        """
        with get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO video_library
                    (state, document_type, youtube_url, created_at, updated_at)
                VALUES (?, ?, NULL, ?, ?)
                """,
                (state, document_type.value, now_utc(), now_utc()),
            )

            conn.execute(
                """
                INSERT INTO outreach
                    (place_id, company_name, status, created_at, updated_at)
                VALUES (?, ?, 'video_needed', ?, ?)
                ON CONFLICT(place_id) DO UPDATE SET
                    status = 'video_needed',
                    updated_at = excluded.updated_at
                """,
                (place_id, company_name, now_utc(), now_utc()),
            )

    def _build_prompt(
        self,
        company_name: str,
        company_summary: str,
        document_type: DocumentType,
        state: str,
        city: str,
        decision_maker_name: Optional[str],
        decision_maker_title: Optional[str],
        youtube_url: str,
    ) -> str:
        recipient = decision_maker_name or "there"
        first_name = recipient.split()[0] if decision_maker_name else "there"
        title_context = f" ({decision_maker_title})" if decision_maker_title else ""

        document_type_descriptions = {
            DocumentType.STORMWATER: "stormwater management manuals, drainage design standards, and water quality regulations",
            DocumentType.LAND_DEVELOPMENT: "land development codes, subdivision regulations, and site design standards",
            DocumentType.STRUCTURAL: "structural design standards, building codes, and load specifications",
            DocumentType.TRANSPORTATION: "road design standards, traffic engineering guidelines, and highway manuals",
            DocumentType.MUNICIPAL: "public works standards, utility design manuals, and municipal codes",
            DocumentType.GEOTECHNICAL: "geotechnical investigation standards, soil classification codes, and foundation design guidelines",
            DocumentType.UNKNOWN: "civil engineering design standards and regulatory codes",
        }

        doc_description = document_type_descriptions.get(
            document_type, "civil engineering regulatory documents"
        )

        return f"""
    You are writing a cold email on behalf of Emmanuel Uwadiae, who studied BEng Civil and Structural Engineering at the University of Leeds before transitioning into AI engineering. He built SwiftCiv after watching engineers spend hours hunting through regulatory PDFs for a single quote.

    SwiftCiv lets civil engineers upload regulatory PDFs and get verbatim quotes with exact page and section citations instantly. No paraphrasing, no hallucinations. Just the exact text from the document with the page number.

    Write a cold email to {first_name}{title_context} at {company_name} in {city}, {state}.

    Company context: {company_summary}

    This company primarily works with {doc_description}.

    The email must follow this exact structure:
    1. Open with a specific reference to what {company_name} does — show you know their work. One sentence.
    2. One sentence introducing Emmanuel — he studied BEng Civil and Structural Engineering at the University of Leeds before moving into AI engineering, and built SwiftCiv after watching engineers spend hours hunting through regulatory PDFs for a single quote.
    3. One sentence explaining what SwiftCiv does — verbatim quotes with exact page and section citations from uploaded PDFs, nothing paraphrased.
    4. Reference the short demo recorded specifically for {document_type.value} documents relevant to {state} and include this YouTube link naturally: {youtube_url}
    5. End with this exact call to action: "Would a quick 10-minute call be worth your time? Book here: {self.calendly_link}"
    6. Close with this exact signature:

    Emmanuel Uwadiae
    BEng Civil & Structural Engineering
    fulodev.com

    Rules:
    - The YouTube link and the Calendly link must be two completely different URLs — never use the same URL twice
    - Be under 180 words total excluding the signature
    - Sound like a human wrote it — no corporate language, no buzzwords
    - First person as Emmanuel
    - Do not use the word "verbatim" in the email body — find a natural way to express it

    Also write a subject line specific to {company_name} and their work. Not generic. Not salesy.

    Return ONLY a valid JSON object with two fields: "subject" and "body".
    No preamble, no explanation, no markdown code fences.

    Example format:
    {{"subject": "Quick question about land dev lookups at {company_name}", "body": "Hi {first_name},\\n\\n..."}}
    """

    @retry(
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
    )
    def _call_grok(self, prompt: str) -> dict:
        """
        call grok api and return parsed response.
        """
        response = self.session.post(
            GROK_API_URL,
            json={
                "model": GROK_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an expert cold email copywriter. "
                            "You write concise, specific, human emails that get replies. "
                            "You always return valid JSON and nothing else."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "temperature": 0.7,
                "max_tokens": 800,
            },
        )

        response.raise_for_status()

        return response.json()

    def _parse_response(
        self,
        response: dict,
        decision_maker_name: Optional[str],
        decision_maker_email: Optional[str],
        youtube_url: str,
    ) -> EmailDraft:
        """
        parse grok response into an EmailDraft
        """
        try:
            content = response["choices"][0]["message"]["content"].strip()

            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]

            data = json.loads(content)

            return EmailDraft(
                subject=data["subject"],
                body=data["body"],
                recipient_name=decision_maker_name,
                recipient_email=decision_maker_email,
                youtube_url=youtube_url,
            )

        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse Grok personalisation response: {e}")
            raise

    def _save_draft(self, place_id: str, company_name: str, draft: EmailDraft) -> None:
        """
        save email draft to outreach table.
        """
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO outreach (
                    place_id, company_name, recipient_email,
                    recipient_name, status, email_subject,
                    email_body, youtube_url, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
                ON CONFLICT(place_id) DO UPDATE SET
                    recipient_email = COALESCE(excluded.recipient_email, outreach.recipient_email),
                    recipient_name = COALESCE(excluded.recipient_name, outreach.recipient_name),
                    email_subject = excluded.email_subject,
                    email_body = excluded.email_body,
                    youtube_url = excluded.youtube_url,
                    status = 'queued',
                    updated_at = excluded.updated_at
                """,
                (
                    place_id,
                    company_name,
                    draft.recipient_email,
                    draft.recipient_name,
                    draft.subject,
                    draft.body,
                    draft.youtube_url,
                    now_utc(),
                    now_utc(),
                ),
            )

        logger.info(
            f"Email draft saved for {company_name} — "
            f"subject: {draft.subject[:50]}..."
        )
