"""Fetch customer reviews from Apple's public RSS/JSON feed.

This is the unauthenticated alternative to the App Store Connect API.
It returns up to ~500 of the most recent reviews per territory (10 pages
of 50 reviews each), with no authentication required.

Feed format:
  https://itunes.apple.com/{territory}/rss/customerreviews/page={n}/id={app_id}/sortby=mostrecent/json
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 30
MAX_RETRIES = 3
MAX_PAGES = 10  # Apple's RSS feed caps at 10 pages of ~50 reviews each


def fetch_all_reviews(app_id: str, territory: str = "us", known_ids: set = None) -> list:
    """
    Fetch customer reviews from Apple's public RSS feed.

    Paginates up to MAX_PAGES. If known_ids is provided, stops early when
    an entire page of reviews has already been seen.

    Returns a list of dicts ready for database insertion.
    """
    if known_ids is None:
        known_ids = set()

    all_reviews = []

    for page in range(1, MAX_PAGES + 1):
        url = (
            f"https://itunes.apple.com/{territory}/rss/customerreviews/"
            f"page={page}/id={app_id}/sortby=mostrecent/json"
        )

        data = _request_with_retry(url)
        if data is None:
            logger.warning("Failed to fetch page %d, stopping.", page)
            break

        entries = data.get("feed", {}).get("entry", [])

        # Apple's RSS sometimes wraps a single app-info entry at position 0.
        # Skip entries that don't have im:rating (those are app metadata, not reviews).
        review_entries = [e for e in entries if "im:rating" in e]

        if not review_entries:
            logger.info("Page %d has no reviews, stopping.", page)
            break

        page_reviews = []
        all_known = True

        for entry in review_entries:
            review_id = entry["id"]["label"]
            review = {
                "id": review_id,
                "rating": int(entry["im:rating"]["label"]),
                "title": entry.get("title", {}).get("label"),
                "body": entry.get("content", {}).get("label"),
                "reviewer": entry.get("author", {}).get("name", {}).get("label"),
                "territory": territory.upper(),
                "app_version": entry.get("im:version", {}).get("label"),
                "created_date": entry.get("updated", {}).get("label"),
            }

            if review_id not in known_ids:
                all_known = False

            page_reviews.append(review)

        all_reviews.extend(page_reviews)
        logger.info("Page %d: fetched %d reviews", page, len(page_reviews))

        # Early exit if we've seen all reviews on this page
        if all_known and known_ids:
            logger.info("All reviews on page %d already known, stopping pagination.", page)
            break

    return all_reviews


def _request_with_retry(url: str) -> dict | None:
    """Make a GET request with exponential backoff on retryable errors."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, timeout=TIMEOUT)

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code in (429, 500, 502, 503, 504):
                wait = min(2**attempt, 30)
                logger.warning(
                    "HTTP %d on attempt %d, retrying in %ds...",
                    resp.status_code, attempt + 1, wait,
                )
                time.sleep(wait)
                continue

            logger.error("Unexpected HTTP %d: %s", resp.status_code, resp.text[:200])
            return None

        except requests.RequestException as e:
            wait = min(2**attempt, 30)
            logger.warning("Request error on attempt %d: %s, retrying in %ds...", attempt + 1, e, wait)
            time.sleep(wait)

    logger.error("Max retries exceeded.")
    return None
