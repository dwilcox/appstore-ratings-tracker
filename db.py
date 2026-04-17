"""SQLite database schema and data access for App Store reviews."""

import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id             TEXT PRIMARY KEY,
    rating         INTEGER NOT NULL,
    title          TEXT,
    body           TEXT,
    reviewer       TEXT,
    territory      TEXT,
    app_version    TEXT,
    created_date   TEXT NOT NULL,
    fetched_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_created_date ON reviews(created_date);

CREATE TABLE IF NOT EXISTS rating_snapshots (
    snapshot_date    TEXT NOT NULL,
    app_id           TEXT NOT NULL,
    store_avg_rating REAL,
    store_rating_count INTEGER,
    store_rating_count_current_version INTEGER,
    current_version  TEXT,
    fetched_at       TEXT NOT NULL,
    PRIMARY KEY (snapshot_date, app_id)
);

CREATE TABLE IF NOT EXISTS star_distribution_snapshots (
    snapshot_date    TEXT NOT NULL,
    app_id           TEXT NOT NULL,
    star_1           INTEGER DEFAULT 0,
    star_2           INTEGER DEFAULT 0,
    star_3           INTEGER DEFAULT 0,
    star_4           INTEGER DEFAULT 0,
    star_5           INTEGER DEFAULT 0,
    fetched_at       TEXT NOT NULL,
    PRIMARY KEY (snapshot_date, app_id)
);

CREATE TABLE IF NOT EXISTS daily_snapshots (
    snapshot_date    TEXT NOT NULL,
    app_id           TEXT NOT NULL,
    avg_rating       REAL,
    total_count      INTEGER,
    star_1           INTEGER DEFAULT 0,
    star_2           INTEGER DEFAULT 0,
    star_3           INTEGER DEFAULT 0,
    star_4           INTEGER DEFAULT 0,
    star_5           INTEGER DEFAULT 0,
    new_today        INTEGER DEFAULT 0,
    avg_rating_today REAL,
    computed_at      TEXT NOT NULL,
    PRIMARY KEY (snapshot_date, app_id)
);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    """Create tables if needed and return a connection."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def get_known_review_ids(conn: sqlite3.Connection) -> set:
    """Return the set of all review IDs already stored."""
    cursor = conn.execute("SELECT id FROM reviews")
    return {row[0] for row in cursor}


def insert_reviews(conn: sqlite3.Connection, reviews: list) -> int:
    """Insert reviews, skipping duplicates. Returns count of newly inserted rows."""
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for r in reviews:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO reviews (id, rating, title, body, reviewer, territory, app_version, created_date, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r["id"],
                    r["rating"],
                    r.get("title"),
                    r.get("body"),
                    r.get("reviewer"),
                    r.get("territory"),
                    r.get("app_version"),
                    r["created_date"],
                    now,
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                inserted += 1
        except sqlite3.Error:
            continue
    conn.commit()
    return inserted


def upsert_rating_snapshot(conn: sqlite3.Connection, snapshot_date: str, app_id: str, data: dict):
    """Store aggregate rating data from the App Store lookup API."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO rating_snapshots "
        "(snapshot_date, app_id, store_avg_rating, store_rating_count, store_rating_count_current_version, current_version, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            snapshot_date,
            app_id,
            data.get("averageUserRating"),
            data.get("userRatingCount"),
            data.get("userRatingCountForCurrentVersion"),
            data.get("version"),
            now,
        ),
    )
    conn.commit()


def upsert_star_distribution(conn: sqlite3.Connection, snapshot_date: str, app_id: str, data: dict):
    """Store star distribution data scraped from the App Store page."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO star_distribution_snapshots "
        "(snapshot_date, app_id, star_1, star_2, star_3, star_4, star_5, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snapshot_date,
            app_id,
            data["star_1"],
            data["star_2"],
            data["star_3"],
            data["star_4"],
            data["star_5"],
            now,
        ),
    )
    conn.commit()


def upsert_daily_snapshot(conn: sqlite3.Connection, snapshot_date: str, app_id: str):
    """Compute aggregates from reviews and store as a daily snapshot."""
    now = datetime.now(timezone.utc).isoformat()

    # Cumulative stats
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_count,
            AVG(rating) AS avg_rating,
            SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END),
            SUM(CASE WHEN rating = 2 THEN 1 ELSE 0 END),
            SUM(CASE WHEN rating = 3 THEN 1 ELSE 0 END),
            SUM(CASE WHEN rating = 4 THEN 1 ELSE 0 END),
            SUM(CASE WHEN rating = 5 THEN 1 ELSE 0 END)
        FROM reviews
        """
    ).fetchone()

    total_count = row[0]
    avg_rating = row[1]
    star_1, star_2, star_3, star_4, star_5 = row[2], row[3], row[4], row[5], row[6]

    # Today's stats
    daily = conn.execute(
        "SELECT COUNT(*), AVG(rating) FROM reviews WHERE date(created_date) = ?",
        (snapshot_date,),
    ).fetchone()

    new_today = daily[0]
    avg_rating_today = daily[1]

    conn.execute(
        "INSERT OR REPLACE INTO daily_snapshots "
        "(snapshot_date, app_id, avg_rating, total_count, star_1, star_2, star_3, star_4, star_5, new_today, avg_rating_today, computed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snapshot_date,
            app_id,
            avg_rating,
            total_count,
            star_1,
            star_2,
            star_3,
            star_4,
            star_5,
            new_today,
            avg_rating_today,
            now,
        ),
    )
    conn.commit()
