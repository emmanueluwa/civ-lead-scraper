"""
Database models for the sales agent pipeline.
Tracks outreach state, video mappings, and booked calls.
Uses SQLite for simplicity and portability.
All timestamps are UTC.
"""

import sqlite3
import os
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "sales_agent.db")


class OutreachStatus(Enum):
    PENDING = "pending"  # lead found, not yet contacted
    RESEARCHED = "researched"  # company researched and classified
    VIDEO_NEEDED = "video_needed"  # video not yet recorded for this group
    QUEUED = "queued"  # ready to send, video available
    SENT = "sent"  # initial email sent
    FOLLOW_UP_1 = "follow_up_1"  # first follow up sent
    FOLLOW_UP_2 = "follow_up_2"  # second follow up sent
    FOLLOW_UP_3 = "follow_up_3"  # third follow up sent
    REPLIED = "replied"  # prospect replied
    INTERESTED = "interested"  # prospect expressed interest
    BOOKED = "booked"  # call booked
    COLD = "cold"  # no response after all follow ups
    UNSUBSCRIBED = "unsubscribed"  # prospect asked to be removed


class DocumentType(Enum):
    STORMWATER = "stormwater"
    LAND_DEVELOPMENT = "land_development"
    STRUCTURAL = "structural"
    TRANSPORTATION = "transportation"
    MUNICIPAL = "municipal"
    GEOTECHNICAL = "geotechnical"
    UNKNOWN = "unknown"


@contextmanager
def get_connection():
    """Context manager for SQLite connections with automatic cleanup."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """
    Initialise all sales agent tables.
    Safe to call multiple times — uses IF NOT EXISTS.
    """
    with get_connection() as conn:
        conn.executescript("""
            -- tracks research and classification of each company
            CREATE TABLE IF NOT EXISTS company_research (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id TEXT UNIQUE NOT NULL,
                company_name TEXT NOT NULL,
                website TEXT,
                phone TEXT,
                address TEXT,
                city TEXT,
                state TEXT,
                country TEXT DEFAULT 'USA',
                document_type TEXT NOT NULL DEFAULT 'unknown',
                decision_maker_name TEXT,
                decision_maker_title TEXT,
                decision_maker_email TEXT,
                company_summary TEXT,
                researched_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            -- tracks outreach state per company
            CREATE TABLE IF NOT EXISTS outreach (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id TEXT UNIQUE NOT NULL,
                company_name TEXT NOT NULL,
                recipient_email TEXT,
                recipient_name TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                email_subject TEXT,
                email_body TEXT,
                youtube_url TEXT,
                sent_at TEXT,
                follow_up_1_at TEXT,
                follow_up_2_at TEXT,
                follow_up_3_at TEXT,
                replied_at TEXT,
                reply_content TEXT,
                booked_at TEXT,
                cold_at TEXT,
                hubspot_contact_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (place_id) REFERENCES company_research(place_id)
            );

            -- maps document type + state to youtube video URL
            -- one row per state + document_type combination
            CREATE TABLE IF NOT EXISTS video_library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state TEXT NOT NULL,
                document_type TEXT NOT NULL,
                youtube_url TEXT,
                suggested_questions TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(state, document_type)
            );

            -- tracks booked calls
            CREATE TABLE IF NOT EXISTS booked_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id TEXT NOT NULL,
                company_name TEXT NOT NULL,
                contact_name TEXT,
                contact_email TEXT,
                scheduled_at TEXT,
                calendly_event_id TEXT UNIQUE,
                call_notes TEXT,
                outcome TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (place_id) REFERENCES company_research(place_id)
            );

            -- tracks all emails sent for audit trail
            CREATE TABLE IF NOT EXISTS email_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                outreach_id INTEGER NOT NULL,
                email_type TEXT NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                gmail_message_id TEXT,
                sent_at TEXT NOT NULL,
                FOREIGN KEY (outreach_id) REFERENCES outreach(id)
            );

            -- indexes for common queries
            CREATE INDEX IF NOT EXISTS idx_outreach_status
                ON outreach(status);

            CREATE INDEX IF NOT EXISTS idx_outreach_place_id
                ON outreach(place_id);

            CREATE INDEX IF NOT EXISTS idx_company_research_state_doctype
                ON company_research(state, document_type);

            CREATE INDEX IF NOT EXISTS idx_video_library_state_doctype
                ON video_library(state, document_type);

            CREATE INDEX IF NOT EXISTS idx_booked_calls_place_id
                ON booked_calls(place_id);
        """)

    logger.info(f"Sales agent database initialised at {DB_PATH}")


def now_utc() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("✅ Database initialised successfully")
