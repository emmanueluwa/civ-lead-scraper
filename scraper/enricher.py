"""
website enrichment using playwright
visits company websites to extract named contacts and email addresses.

runs after google places discovery - adds human contact details to leads.
uses headless chronium for js heavy sites
"""

import logging
import re
from typing import Optional
from dataclasses import dataclass

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

# pages most likely to have contact information
CONTACT_PAGE_PATHS = [
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/people",
    "/staff",
]

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# emails to ignore — generic addresses not worth adding
IGNORED_EMAIL_PREFIXES = (
    "info@",
    "contact@",
    "admin@",
    "hello@",
    "support@",
    "mail@",
    "office@",
    "enquiries@",
    "noreply@",
    "no-reply@",
)


@dataclass
class EnrichedContact:
    first_name: Optional[str]
    last_name: Optional[str]
    email: Optional[str]
    title: Optional[str]


class Enricher:
    def __init__(self):
        self.playwright = None
        self.browser = None

    def __enter__(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        return self

    def __exit__(self, *args):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def enrich(self, website_url: str) -> EnrichedContact:
        """
        visit company website and extract best contact found
        returns EnrichedContact, field will be None if nothing is found
        """
        if not website_url:
            return EnrichedContact(None, None, None, None)

        # normalise url
        if not website_url.startswith(("http://", "https://")):
            website_url = f"https://{website_url}"

        try:
            # try contact/about pages first
            contact = self._try_contact_pages(website_url)
            if contact.email:
                return contact

            # fall back to homepage
            contact = self._scrape_page(website_url)

            return contact

        except Exception as e:
            logger.warning(f"enrichment failed for {website_url}: {e}")
            return EnrichedContact(None, None, None, None)

    def _try_contact_pages(self, base_url: str) -> EnrichedContact:
        """try known contct page paths before falling back to homepage"""
        base = base_url.rstrip("/")

        for path in CONTACT_PAGE_PATHS:
            try:
                url = f"{base}{path}"
                contact = self._scrape_page(url)
                if contact.email:
                    logger.info(f"Found contact at {url}")
                    return contact
            except Exception:
                continue

        return EnrichedContact(None, None, None, None)

    def _scrape_page(self, url: str) -> EnrichedContact:
        """
        scrape single page for contact info
        extract emails, names, and job titles
        """
        context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        try:
            page.goto(url, timeout=10000, wait_until="domcontentloaded")
            content = page.inner_text("body")

            email = self._extract_best_email(content, page)
            first_name, last_name, title = self._extract_name_and_title(content)

            return EnrichedContact(
                first_name=first_name,
                last_name=last_name,
                email=email,
                title=title,
            )

        except PlaywrightTimeout:
            logger.warning(f"Timeout scraping {url}")
            return EnrichedContact(None, None, None, None)
        finally:
            context.close()

    def _extract_best_email(self, text: str, page) -> Optional[str]:
        """
        extract the most useful email from page content.
        prefer named personal emails over generic ones.
        check mailto links first as they are most reliable.
        """
        # check mailto links first
        try:
            mailto_links = page.eval_on_selector_all(
                "a[href^='mailto:']", "els => els.map(el => el.href)"
            )
            for link in mailto_links:
                email = link.replace("mailto:", "").split("?")[0].strip()
                if email and not any(
                    email.lower().startswith(p) for p in IGNORED_EMAIL_PREFIXES
                ):
                    return email
        except Exception:
            pass

        # fall back to regex on page text
        emails = EMAIL_PATTERN.findall(text)
        for email in emails:
            if not any(email.lower().startswith(p) for p in IGNORED_EMAIL_PREFIXES):
                return email

        # last resort — return generic email if nothing personal found
        emails = EMAIL_PATTERN.findall(text)
        if emails:
            return emails[0]

        return None

    def _extract_name_and_title(
        self, text: str
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        attempt to extract a named contact and their title from page text.
        look for patterns like 'John Smith, PE' or 'Principal Engineer: John Smith'
        returns None if nothing reliable found.
        """
        title_keywords = [
            "Principal Engineer",
            "Senior Engineer",
            "Project Engineer",
            "Director",
            "President",
            "Founder",
            "Partner",
            "PE,",
            "P.E.",
        ]

        lines = text.split("\n")
        for i, line in enumerate(lines):
            line = line.strip()
            for keyword in title_keywords:
                if keyword.lower() in line.lower() and len(line) < 80:
                    # try to extract name from surrounding lines
                    name_line = lines[i - 1].strip() if i > 0 else ""
                    if name_line and len(name_line.split()) in (2, 3):
                        parts = name_line.split()
                        return parts[0], parts[-1], keyword.replace(",", "").strip()

        return None, None, None
