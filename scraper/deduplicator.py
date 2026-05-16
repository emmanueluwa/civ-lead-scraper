"""
sqlite based deduplication layer
prevents duplicate leads reaching hubspot
"""

from bisect import insort
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "leads.db")


@contextmanager
def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class Deduplicator:
    def __init__(self):
        self._init_db()

    def _init_db(self):
        """create tables if they do not exist"""
        with get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS seen_places (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    place_id TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    phone TEXT,
                    website TEXT,
                    first_seen_at TEXT NOT NULL,
                    pushed_to_hubspot INTEGER DEFAULT 0
                );
                               
                CREATE TABLE IF NOT EXISTS seen_phones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT UNIQUE NOT NULL,
                    first_seen_at TEXT NOT NULL
                );
                               
                CREATE INDEX IF NOT EXISTS idx_seen_places_place_id
                    ON seen_places(place_id);

                CREATE INDEX IF NOT EXISTS idx_seen_phones_phone
                    ON seen_phones(phone);
        """)
        logger.info(f"Deduplicator initialised — DB at {DB_PATH}")

    def is_duplicate(self, place_id: str, phone: str) -> bool:
        """
        return true if place or number have been seen before
        check place_id and phone number independently
        """
        with get_connection() as conn:
            # check place_id
            row = conn.execute(
                "SELECT id FROM seen_places WHERE place_id = ?", (place_id,)
            ).fetchone()
            if row:
                return True

            # check phone number
            if phone:
                row = conn.ececute(
                    "SELECT id FROM seen_phones WHERE phone = ?", (phone)
                ).fetchone()
                if row:
                    return True

        return False

    def mark_seen(self, place_id: str, name: str, phone: str, website: str):
        """record a place and phone number as seen"""
        now = datetime.now(datetime.timezone.utc).isoformat()

        with get_connection() as conn:
            # insert found place
            conn.execute(
                """
                    INSERT OR IGNORE INTO seen_places
                        (place_id, name, phone, website, first_seen_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                (place_id, name, phone, website, now),
            )

            # insert found phone number
            if phone:
                conn.execute(
                    """
                        INSERT OR IGNORE INTO seen_phones (phone, first_seen_at)
                        VALUES (?, ?)
                        """,
                    (phone, now),
                )

    def mark_pushed(self, place_id: str):
        """mark place as successfully pushed to hubspot"""
        with get_connection() as conn:
            conn.execute(
                "UPDATE seen_places SET pushed_to_hubspot = 1 WHERE place_id = ?",
                (place_id,),
            )

    def get_stats(self) -> dict:
        """return deduplication stats for the daily report"""
        with get_connection() as conn:
            total_seen = conn.execute("SELECT COUNT(*) FROM seen_places").fetchone()[0]

            total_pushed = conn.execute(
                "SELECT COUNT(*) FROM seen_places WHERE pushed_to_hubspot = 1"
            ).fetchone()[0]

            total_phones = conn.execute("SELECT COUNT(*) FROM seen_phones").fetchone()[
                0
            ]

        return {
            "total_seen": total_seen,
            "total_pushed": total_pushed,
            "total_phones": total_phones,
        }
