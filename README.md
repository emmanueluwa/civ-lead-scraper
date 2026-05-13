# Civ Lead Scraper

## What is this?

Lead scraper for civil engineering, structural engineering and similar firms in the USA

300 will be scraped each morning for me to cold call for that day

## Tech stack:

- Python — core language
- Google Places API (New) — primary data source for business phone numbers
- Playwright — headless browser for website contact extraction (more reliable than BeautifulSoup for modern sites)
- HubSpot API — for pushing leads to CRM
- SQLite — local deduplication database, tracks every number and company ever scraped
- Celery + Redis — task queue so searches run in parallel to improve speed
- Telegram Bot — daily report delivery
- Docker — containerised so it runs anywhere
- Cron job — runs automatically every morning at 8am
