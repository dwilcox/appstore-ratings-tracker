#!/usr/bin/env python3
"""Collect App Store reviews and store them in a local SQLite database."""

import datetime
import logging
import logging.handlers
import os
import sys

from appstore_lookup import fetch_app_ratings
from appstore_scrape import fetch_star_distribution
from rss_client import fetch_all_reviews
from db import get_known_review_ids, init_db, insert_reviews, upsert_daily_snapshot, upsert_rating_snapshot, upsert_star_distribution

# Log rotation: 1 MB max, keep 3 backups
log_dir = os.path.dirname(os.path.abspath(__file__))
log_path = os.path.join(log_dir, "collect.log")

handler_file = logging.handlers.RotatingFileHandler(
    log_path, maxBytes=1_000_000, backupCount=3
)
handler_console = logging.StreamHandler()

formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
handler_file.setFormatter(formatter)
handler_console.setFormatter(formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[handler_file, handler_console],
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    """Load configuration from environment variables, falling back to a .env file."""
    # Try loading .env file if it exists
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())

    required = ["ASC_APP_ID"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        logger.error("Missing required config: %s", ", ".join(missing))
        logger.error("Set them as environment variables or in a .env file.")
        sys.exit(1)

    return {
        "app_id": os.environ["ASC_APP_ID"],
        "db_path": os.environ.get("DB_PATH", "./reviews.db"),
    }


def main():
    config = load_config()

    logger.info("Initializing database at %s", config["db_path"])
    conn = init_db(config["db_path"])

    known_ids = get_known_review_ids(conn)
    logger.info("Database contains %d existing reviews", len(known_ids))

    logger.info("Fetching reviews from Apple's public RSS feed...")
    reviews = fetch_all_reviews(config["app_id"], known_ids=known_ids)
    logger.info("Fetched %d reviews from feed", len(reviews))

    inserted = insert_reviews(conn, reviews)
    logger.info("Inserted %d new reviews", inserted)

    today = datetime.date.today().isoformat()
    upsert_daily_snapshot(conn, today, config["app_id"])
    logger.info("Updated review snapshot for %s", today)

    # Fetch aggregate ratings (includes ratings without reviews)
    logger.info("Fetching aggregate ratings from App Store...")
    ratings = fetch_app_ratings(config["app_id"])
    if ratings:
        upsert_rating_snapshot(conn, today, config["app_id"], ratings)
        logger.info(
            "App Store ratings: %.1f avg from %d total ratings (%d for current version %s)",
            ratings["averageUserRating"] or 0,
            ratings["userRatingCount"] or 0,
            ratings["userRatingCountForCurrentVersion"] or 0,
            ratings.get("version", "?"),
        )
    else:
        logger.warning("Could not fetch aggregate ratings.")

    # Fetch star distribution from App Store page
    logger.info("Fetching star distribution from App Store page...")
    star_dist = fetch_star_distribution(config["app_id"])
    if star_dist:
        upsert_star_distribution(conn, today, config["app_id"], star_dist)
        total = sum(star_dist.values())
        logger.info(
            "Star distribution: 5★=%d  4★=%d  3★=%d  2★=%d  1★=%d  (total: %d)",
            star_dist["star_5"], star_dist["star_4"], star_dist["star_3"],
            star_dist["star_2"], star_dist["star_1"], total,
        )
    else:
        logger.warning("Could not fetch star distribution.")

    # Print review summary
    row = conn.execute(
        "SELECT total_count, avg_rating, new_today, avg_rating_today FROM daily_snapshots WHERE snapshot_date = ? AND app_id = ?",
        (today, config["app_id"]),
    ).fetchone()
    if row:
        logger.info(
            "Review summary: %d reviews with text, %.2f avg review rating, %d new today",
            row[0],
            row[1] or 0,
            row[2],
        )

    # Auto-generate HTML report
    try:
        from report import get_db, generate_html
        report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report.html")
        report_conn = get_db(config["db_path"])
        generate_html(report_conn, report_path)
        report_conn.close()
        logger.info("HTML report updated at %s", report_path)
    except Exception as e:
        logger.warning("Could not generate HTML report: %s", e)

    conn.close()
    logger.info("Done.")


if __name__ == "__main__":
    main()
