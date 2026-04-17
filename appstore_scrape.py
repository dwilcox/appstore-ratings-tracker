"""Fetch star rating distribution from the App Store product page."""

import json
import logging
import re

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 15


def fetch_star_distribution(app_id: str, country: str = "us") -> dict | None:
    """
    Fetch the star rating breakdown from the App Store product page.

    The page embeds structured JSON with a ratingCounts array
    ordered from 5-star to 1-star.

    Returns a dict with keys: star_1 through star_5, or None on failure.
    """
    url = f"https://apps.apple.com/{country}/app/id{app_id}"

    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={"Accept-Language": "en-US"})
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch App Store page: %s", e)
        return None

    # Look for ratingCounts in the page source
    match = re.search(r'"ratingCounts"\s*:\s*\[(\d+),(\d+),(\d+),(\d+),(\d+)\]', resp.text)
    if not match:
        logger.error("Could not find ratingCounts in App Store page.")
        return None

    # ratingCounts is ordered 5-star to 1-star
    counts = [int(match.group(i)) for i in range(1, 6)]

    return {
        "star_5": counts[0],
        "star_4": counts[1],
        "star_3": counts[2],
        "star_2": counts[3],
        "star_1": counts[4],
    }
