"""Fetch aggregate rating data from the public App Store lookup API."""

import logging

import requests

logger = logging.getLogger(__name__)

LOOKUP_URL = "https://itunes.apple.com/lookup"
TIMEOUT = 15


def fetch_app_ratings(app_id: str, country: str = "us") -> dict | None:
    """
    Fetch aggregate rating info for an app from the iTunes lookup API.

    Returns a dict with keys: averageUserRating, userRatingCount,
    userRatingCountForCurrentVersion, version. Returns None on failure.
    """
    try:
        resp = requests.get(
            LOOKUP_URL,
            params={"id": app_id, "country": country},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.error("Failed to fetch App Store lookup data: %s", e)
        return None

    results = data.get("results", [])
    if not results:
        logger.error("No results from App Store lookup for app ID %s", app_id)
        return None

    app = results[0]
    return {
        "averageUserRating": app.get("averageUserRating"),
        "userRatingCount": app.get("userRatingCount"),
        "userRatingCountForCurrentVersion": app.get("userRatingCountForCurrentVersion"),
        "version": app.get("version"),
    }
