"""
telegram notification service
daily report on lead generation pipeline run
"""

import logging
import os
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self):
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID")

        if not self.bot_token or not self.chat_id:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in environment"
            )

        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def send_daily_report(
        self,
        leads_found: int,
        leads_pushed: int,
        duplicates_skipped: int,
        errors: int,
        cities_searched: int,
        queries_run: int,
        duration_seconds: float,
        db_stats: dict,
        error_details: Optional[list[str]] = None,
    ) -> None:
        """send a formatted daily report to telegram"""
        status_icon = "✅" if errors == 0 else "⚠️"
        duration_mins = round(duration_seconds / 60, 1)

        message = (
            f"{status_icon} *SwiftCiv Lead Scraper — Daily Report*\n"
            f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"*Today's Run*\n"
            f"🏙️ Cities searched: {cities_searched}\n"
            f"🔍 Queries run: {queries_run}\n"
            f"📋 Raw results: {leads_found}\n"
            f"✅ Pushed to HubSpot: {leads_pushed}\n"
            f"♻️ Duplicates skipped: {duplicates_skipped}\n"
            f"❌ Errors: {errors}\n"
            f"⏱️ Duration: {duration_mins} mins\n\n"
            f"*Database Stats*\n"
            f"📦 Total leads ever scraped: {db_stats.get('total_seen', 0)}\n"
            f"📤 Total pushed to HubSpot: {db_stats.get('total_pushed', 0)}\n"
            f"📞 Unique phone numbers: {db_stats.get('total_phones', 0)}\n"
        )

        if error_details:
            message += f"\n*Recent Errors*\n"
            for error in error_details[:5]:
                message += f"• {error}\n"

        self._send(message)

    def send_alert(self, message: str) -> None:
        """alert for critical failues"""
        self._send(f"🚨 *Lead Scraper Alert*\n\n{message}")

    def _send(self, message: str) -> None:
        """send message to telegram"""
        try:
            response = requests.post(
                self.api_url,
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )

            response.raise_for_status()

            logger.info("Telegram notification sent successfully")

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send Telegram notification: {e}")
