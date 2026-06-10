"""
reads results.json from email finder and updates sqlite db
leads with emails found are moved to pending for personalisation
leads without emails stay as no_email
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import DB_PATH, now_utc


def update_db():
    results_path = os.path.join(os.path.dirname(__file__), "results.json")

    if not os.path.exists(results_path):
        print("results.json not found — run index.js first")
        return

    with open(results_path) as f:
        results = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    updated = 0
    skipped = 0

    for result in results:
        if result.get("email"):
            conn.execute(
                """
                UPDATE outreach
                SET recipient_email = ?,
                    status = 'pending',
                    updated_at = ?
                WHERE id = ?
                """,
                (result["email"], now_utc(), result["outreach_id"]),
            )
            print(f"Updated {result['company_name']} — {result['email']}")
            updated += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()

    print(f"\nDone — updated={updated} skipped={skipped}")
    print("Run personalisation pipeline to generate emails for updated leads")


if __name__ == "__main__":
    update_db()
