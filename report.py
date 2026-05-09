#!/usr/bin/env python3
"""CLI summary report and HTML chart generation for App Store ratings."""

import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _get_version_history(conn: sqlite3.Connection) -> list:
    """
    Derive per-version rating data from rating_snapshots.

    Uses the delta in total rating count (store_rating_count) across
    version boundaries, since store_rating_count_current_version from
    the iTunes lookup API is unreliable (often equals the total).
    """
    rows = conn.execute(
        "SELECT snapshot_date, current_version, store_rating_count "
        "FROM rating_snapshots ORDER BY snapshot_date"
    ).fetchall()

    if not rows:
        return []

    versions = []
    current_version = rows[0]["current_version"]
    first_seen = rows[0]["snapshot_date"]
    count_at_start = rows[0]["store_rating_count"] or 0

    for r in rows[1:]:
        if r["current_version"] != current_version:
            # Version changed — finalize the previous version
            count_at_end = r["store_rating_count"] or 0
            versions.append({
                "version": current_version,
                "first_seen": first_seen,
                "last_seen": r["snapshot_date"],
                "new_ratings": count_at_end - count_at_start,
            })
            current_version = r["current_version"]
            first_seen = r["snapshot_date"]
            count_at_start = count_at_end

    # Add the current (still-active) version
    latest_count = rows[-1]["store_rating_count"] or 0
    versions.append({
        "version": current_version,
        "first_seen": first_seen,
        "last_seen": rows[-1]["snapshot_date"],
        "new_ratings": latest_count - count_at_start,
    })

    return versions


def _get_daily_rating_deltas(conn: sqlite3.Connection) -> list:
    """
    Compute daily new ratings by star value by diffing consecutive
    star_distribution_snapshots rows.
    """
    rows = conn.execute(
        "SELECT snapshot_date, star_1, star_2, star_3, star_4, star_5 "
        "FROM star_distribution_snapshots ORDER BY snapshot_date"
    ).fetchall()

    if len(rows) < 2:
        return []

    deltas = []
    for i in range(1, len(rows)):
        prev, curr = rows[i - 1], rows[i]
        d1 = curr["star_1"] - prev["star_1"]
        d2 = curr["star_2"] - prev["star_2"]
        d3 = curr["star_3"] - prev["star_3"]
        d4 = curr["star_4"] - prev["star_4"]
        d5 = curr["star_5"] - prev["star_5"]
        total = d1 + d2 + d3 + d4 + d5
        deltas.append({
            "date": curr["snapshot_date"],
            "star_1": max(d1, 0),
            "star_2": max(d2, 0),
            "star_3": max(d3, 0),
            "star_4": max(d4, 0),
            "star_5": max(d5, 0),
            "total": max(total, 0),
        })

    return deltas


def cli_report(conn: sqlite3.Connection):
    """Print a text summary to stdout."""

    # Latest rating snapshot
    rs = conn.execute(
        "SELECT * FROM rating_snapshots ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()

    versions = _get_version_history(conn)

    if rs:
        print("=== App Store Ratings ===")
        print(f"  Date:            {rs['snapshot_date']}")
        print(f"  Average rating:  {rs['store_avg_rating']:.1f}")
        print(f"  Total ratings:   {rs['store_rating_count']:,}")
        if versions:
            v = versions[-1]
            print(f"  Current version: {v['version']} ({v['new_ratings']:,} new ratings since {v['first_seen']})")
        print()
    else:
        print("No rating snapshots found yet.\n")

    # All-ratings star distribution (from App Store page)
    sd = conn.execute(
        "SELECT * FROM star_distribution_snapshots ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()

    if sd:
        stars = [sd["star_1"], sd["star_2"], sd["star_3"], sd["star_4"], sd["star_5"]]
        total = sum(stars)
        print(f"=== Star Distribution (all {total:,} ratings) ===")
        if total > 0:
            max_count = max(stars)
            for i in range(5, 0, -1):
                count = stars[i - 1]
                bar = "#" * int(30 * count / max_count) if max_count > 0 else ""
                pct = 100 * count / total
                print(f"  {i} star: {bar:<30s} {count:>5,} ({pct:.0f}%)")
        print()

    # Review star distribution
    ds = conn.execute(
        "SELECT * FROM daily_snapshots ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()

    if ds:
        total = ds["total_count"]
        print(f"=== Reviews (with text): {total:,} total ===")
        if total > 0:
            stars = [ds["star_1"], ds["star_2"], ds["star_3"], ds["star_4"], ds["star_5"]]
            max_count = max(stars) if max(stars) > 0 else 1
            for i in range(5, 0, -1):
                count = stars[i - 1]
                bar = "#" * int(30 * count / max_count) if max_count > 0 else ""
                pct = 100 * count / total if total > 0 else 0
                print(f"  {i} star: {bar:<30s} {count:>5,} ({pct:.0f}%)")
            print(f"  Average: {ds['avg_rating']:.2f}")
        print()

    # Rating trend (from rating_snapshots over time)
    rows = conn.execute(
        "SELECT snapshot_date, store_avg_rating, store_rating_count "
        "FROM rating_snapshots ORDER BY snapshot_date DESC LIMIT 30"
    ).fetchall()

    if len(rows) >= 2:
        latest = rows[0]
        earliest = rows[-1]
        delta_ratings = latest["store_rating_count"] - earliest["store_rating_count"]
        delta_avg = latest["store_avg_rating"] - earliest["store_avg_rating"]
        days = (
            datetime.fromisoformat(latest["snapshot_date"])
            - datetime.fromisoformat(earliest["snapshot_date"])
        ).days
        print(f"=== Trend (last {days} days) ===")
        print(f"  Rating change:   {delta_avg:+.2f}")
        print(f"  New ratings:     {delta_ratings:+,}")
        print()

    # Version history (reuse from above)
    if versions:
        print("=== New Ratings by Version ===")
        for v in versions:
            print(
                f"  {v['version']:<12s} {v['first_seen']} to {v['last_seen']}  "
                f"{v['new_ratings']:>+6,} ratings"
            )
        print()

    # Daily new ratings by star value
    deltas = _get_daily_rating_deltas(conn)
    if deltas:
        print("=== Daily New Ratings ===")
        print(f"  {'Date':<12s} {'Total':>5s}  {'5★':>4s} {'4★':>4s} {'3★':>4s} {'2★':>4s} {'1★':>4s}")
        print(f"  {'-' * 12} {'-' * 5}  {'-' * 4} {'-' * 4} {'-' * 4} {'-' * 4} {'-' * 4}")
        for d in deltas[-14:]:  # Last 14 days
            print(
                f"  {d['date']:<12s} {d['total']:>5d}  "
                f"{d['star_5']:>4d} {d['star_4']:>4d} {d['star_3']:>4d} "
                f"{d['star_2']:>4d} {d['star_1']:>4d}"
            )
        print()

    # Recent reviews
    recent = conn.execute(
        "SELECT rating, title, body, reviewer, created_date "
        "FROM reviews ORDER BY created_date DESC LIMIT 5"
    ).fetchall()

    if recent:
        print("=== Recent Reviews ===")
        for r in recent:
            stars = "*" * r["rating"]
            date = r["created_date"][:10]
            title = r["title"] or "(no title)"
            body = r["body"] or ""
            if len(body) > 120:
                body = body[:120] + "..."
            print(f"  [{date}] {stars} {title}")
            if body:
                print(f"           {body}")
            print()


def generate_html(conn: sqlite3.Connection, output_path: str):
    """Generate a standalone HTML file with interactive charts."""

    # Gather rating snapshot data
    rating_rows = conn.execute(
        "SELECT snapshot_date, store_avg_rating, store_rating_count, "
        "store_rating_count_current_version, current_version "
        "FROM rating_snapshots ORDER BY snapshot_date"
    ).fetchall()

    rating_dates = [r["snapshot_date"] for r in rating_rows]
    rating_avgs = [r["store_avg_rating"] for r in rating_rows]
    rating_counts = [r["store_rating_count"] for r in rating_rows]

    # Gather review snapshot data
    review_rows = conn.execute(
        "SELECT snapshot_date, avg_rating, total_count, "
        "star_1, star_2, star_3, star_4, star_5, new_today "
        "FROM daily_snapshots ORDER BY snapshot_date"
    ).fetchall()

    review_dates = [r["snapshot_date"] for r in review_rows]
    review_avgs = [r["avg_rating"] for r in review_rows]
    review_new = [r["new_today"] for r in review_rows]

    # All-ratings star distribution (from App Store page)
    all_star_dist = [0, 0, 0, 0, 0]
    sd_row = conn.execute(
        "SELECT * FROM star_distribution_snapshots ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    if sd_row:
        all_star_dist = [sd_row["star_1"], sd_row["star_2"], sd_row["star_3"], sd_row["star_4"], sd_row["star_5"]]

    # Review-only star distribution (latest snapshot)
    star_dist = [0, 0, 0, 0, 0]
    if review_rows:
        latest = review_rows[-1]
        star_dist = [latest["star_1"], latest["star_2"], latest["star_3"], latest["star_4"], latest["star_5"]]

    # Reviews by month (from individual reviews)
    monthly_rows = conn.execute(
        "SELECT strftime('%Y-%m', created_date) AS month, "
        "COUNT(*) AS count, AVG(rating) AS avg "
        "FROM reviews GROUP BY month ORDER BY month"
    ).fetchall()

    monthly_labels = [r["month"] for r in monthly_rows]
    monthly_counts = [r["count"] for r in monthly_rows]
    monthly_avgs = [round(r["avg"], 2) for r in monthly_rows]

    # Version history
    versions = _get_version_history(conn)
    version_labels = [v["version"] for v in versions]
    version_ratings = [v["new_ratings"] for v in versions]
    version_periods = [f'{v["first_seen"]} to {v["last_seen"]}' for v in versions]

    # Ratings per version over time (for line chart)
    version_timeline_dates = [r["snapshot_date"] for r in rating_rows]
    version_timeline_counts = [r["store_rating_count_current_version"] for r in rating_rows]
    version_timeline_names = [r["current_version"] for r in rating_rows]

    # Daily new ratings by star value (limit to last 30 days for readability)
    deltas = _get_daily_rating_deltas(conn)
    recent_deltas = deltas[-30:]
    delta_dates = [d["date"] for d in recent_deltas]
    delta_star_1 = [d["star_1"] for d in recent_deltas]
    delta_star_2 = [d["star_2"] for d in recent_deltas]
    delta_star_3 = [d["star_3"] for d in recent_deltas]
    delta_star_4 = [d["star_4"] for d in recent_deltas]
    delta_star_5 = [d["star_5"] for d in recent_deltas]
    delta_totals = [d["total"] for d in recent_deltas]

    # Weekly aggregation of all daily deltas (full history)
    weekly = {}
    for d in deltas:
        # ISO week key: "YYYY-Www"
        dt = datetime.fromisoformat(d["date"])
        iso_year, iso_week, _ = dt.isocalendar()
        key = f"{iso_year}-W{iso_week:02d}"
        if key not in weekly:
            weekly[key] = {"star_1": 0, "star_2": 0, "star_3": 0, "star_4": 0, "star_5": 0}
        for star in ("star_1", "star_2", "star_3", "star_4", "star_5"):
            weekly[key][star] += d[star]

    weekly_keys = sorted(weekly.keys())
    weekly_star_1 = [weekly[k]["star_1"] for k in weekly_keys]
    weekly_star_2 = [weekly[k]["star_2"] for k in weekly_keys]
    weekly_star_3 = [weekly[k]["star_3"] for k in weekly_keys]
    weekly_star_4 = [weekly[k]["star_4"] for k in weekly_keys]
    weekly_star_5 = [weekly[k]["star_5"] for k in weekly_keys]

    # Latest stats for header
    latest_rating = rating_rows[-1] if rating_rows else None

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>App Store Ratings Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f5f7; color: #1d1d1f; padding: 20px; }}
  .header {{ text-align: center; margin-bottom: 30px; }}
  .header h1 {{ font-size: 28px; font-weight: 600; }}
  .header .subtitle {{ color: #86868b; margin-top: 4px; }}
  .stats {{ display: flex; gap: 16px; justify-content: center; flex-wrap: wrap; margin-bottom: 30px; }}
  .stat-card {{ background: white; border-radius: 12px; padding: 20px 28px; text-align: center; min-width: 160px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .stat-card .value {{ font-size: 32px; font-weight: 600; }}
  .stat-card .label {{ color: #86868b; font-size: 13px; margin-top: 4px; }}
  .charts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(500px, 1fr)); gap: 20px; max-width: 1200px; margin: 0 auto; }}
  .chart-card {{ background: white; border-radius: 12px; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .chart-card h2 {{ font-size: 17px; font-weight: 600; margin-bottom: 16px; }}
  canvas {{ max-height: 300px; }}
  .generated {{ text-align: center; color: #86868b; font-size: 12px; margin-top: 30px; }}
</style>
</head>
<body>

<div class="header">
  <h1>App Store Ratings Report</h1>
  <div class="subtitle">Generated {datetime.now(ZoneInfo("America/New_York")).strftime("%B %d, %Y at %I:%M %p %Z")}</div>
</div>

<div class="stats">
  <div class="stat-card">
    <div class="value">{f"{latest_rating['store_avg_rating']:.1f}" if latest_rating else "—"}</div>
    <div class="label">Average Rating</div>
  </div>
  <div class="stat-card">
    <div class="value">{f"{latest_rating['store_rating_count']:,}" if latest_rating else "—"}</div>
    <div class="label">Total Ratings</div>
  </div>
  <div class="stat-card">
    <div class="value">{latest_rating["current_version"] if latest_rating else "—"}</div>
    <div class="label">Current Version</div>
  </div>
  <div class="stat-card">
    <div class="value">{f"{versions[-1]['new_ratings']:,}" if versions else "—"}</div>
    <div class="label">New Ratings ({versions[-1]["version"] if versions else "—"})</div>
  </div>
</div>

<div class="charts">

  <div class="chart-card">
    <h2>Daily New Ratings by Star (last 30 days)</h2>
    <canvas id="dailyRatingsByStar"></canvas>
  </div>

  <div class="chart-card">
    <h2>Weekly New Ratings by Star (full history)</h2>
    <canvas id="weeklyRatingsByStar"></canvas>
  </div>

  <div class="chart-card">
    <h2>Star Distribution (All Ratings)</h2>
    <canvas id="allStarDist"></canvas>
  </div>

  <div class="chart-card">
    <h2>Average Rating Over Time</h2>
    <canvas id="ratingTrend"></canvas>
  </div>

  <div class="chart-card">
    <h2>Total Ratings Over Time</h2>
    <canvas id="ratingCount"></canvas>
  </div>

  <div class="chart-card">
    <h2>Daily New Ratings (Total)</h2>
    <canvas id="dailyRatingsTotal"></canvas>
  </div>

  <div class="chart-card">
    <h2>Star Distribution (Reviews Only)</h2>
    <canvas id="starDist"></canvas>
  </div>

  <div class="chart-card">
    <h2>Reviews by Month</h2>
    <canvas id="monthlyReviews"></canvas>
  </div>

  <div class="chart-card">
    <h2>Monthly Average Rating (Reviews)</h2>
    <canvas id="monthlyAvg"></canvas>
  </div>

  <div class="chart-card">
    <h2>New Ratings by Version</h2>
    <canvas id="versionRatings"></canvas>
  </div>

  <div class="chart-card">
    <h2>Current Version Ratings Over Time</h2>
    <canvas id="versionTimeline"></canvas>
  </div>

</div>

<div class="charts" style="margin-top: 20px; max-width: 1200px; margin-left: auto; margin-right: auto;">
  <div class="chart-card" style="grid-column: 1 / -1;">
    <h2>Version History</h2>
    <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
      <thead>
        <tr style="border-bottom: 2px solid #e5e5e5; text-align: left;">
          <th style="padding: 8px 12px;">Version</th>
          <th style="padding: 8px 12px;">Period</th>
          <th style="padding: 8px 12px; text-align: right;">New Ratings</th>
        </tr>
      </thead>
      <tbody>
        {"".join(f'<tr style="border-bottom: 1px solid #f0f0f0;"><td style="padding: 8px 12px; font-weight: 500;">{v["version"]}</td><td style="padding: 8px 12px; color: #86868b;">{v["first_seen"]} to {v["last_seen"]}</td><td style="padding: 8px 12px; text-align: right;">{v["new_ratings"]:,}</td></tr>' for v in reversed(versions))}
      </tbody>
    </table>
  </div>
</div>

<div class="generated">Data from {rating_dates[0] if rating_dates else "—"} to {rating_dates[-1] if rating_dates else "—"}</div>

<script>
const colors = {{
  blue: "rgba(0, 122, 255, 1)",
  blueFill: "rgba(0, 122, 255, 0.1)",
  green: "rgba(52, 199, 89, 1)",
  greenFill: "rgba(52, 199, 89, 0.1)",
  bars: ["rgba(255, 59, 48, 0.85)", "rgba(255, 149, 0, 0.85)", "rgba(255, 204, 0, 0.85)", "rgba(52, 199, 89, 0.85)", "rgba(0, 122, 255, 0.85)"],
}};

const defaultOpts = {{
  responsive: true,
  plugins: {{ legend: {{ display: false }} }},
  scales: {{
    x: {{
      grid: {{ display: false }},
      ticks: {{ maxTicksLimit: 12, autoSkip: true, maxRotation: 0 }},
    }},
    y: {{ beginAtZero: false }},
  }},
}};

// Rating trend
new Chart(document.getElementById("ratingTrend"), {{
  type: "line",
  data: {{
    labels: {json.dumps(rating_dates)},
    datasets: [{{
      data: {json.dumps(rating_avgs)},
      borderColor: colors.blue,
      backgroundColor: colors.blueFill,
      fill: true,
      tension: 0.3,
      pointRadius: {json.dumps(3 if len(rating_dates) < 60 else 0)},
    }}],
  }},
  options: {{ ...defaultOpts, scales: {{ ...defaultOpts.scales, y: {{ min: 0, max: 5, ticks: {{ stepSize: 1 }} }} }} }},
}});

// Rating count
new Chart(document.getElementById("ratingCount"), {{
  type: "line",
  data: {{
    labels: {json.dumps(rating_dates)},
    datasets: [{{
      data: {json.dumps(rating_counts)},
      borderColor: colors.green,
      backgroundColor: colors.greenFill,
      fill: true,
      tension: 0.3,
      pointRadius: {json.dumps(3 if len(rating_dates) < 60 else 0)},
    }}],
  }},
  options: defaultOpts,
}});

// All-ratings star distribution
new Chart(document.getElementById("allStarDist"), {{
  type: "bar",
  data: {{
    labels: ["1 Star", "2 Stars", "3 Stars", "4 Stars", "5 Stars"],
    datasets: [{{
      data: {json.dumps(all_star_dist)},
      backgroundColor: colors.bars,
      borderRadius: 6,
    }}],
  }},
  options: {{ ...defaultOpts, scales: {{ ...defaultOpts.scales, y: {{ beginAtZero: true }} }} }},
}});

// Daily new ratings by star (stacked bar)
new Chart(document.getElementById("dailyRatingsByStar"), {{
  type: "bar",
  data: {{
    labels: {json.dumps(delta_dates)},
    datasets: [
      {{ label: "5★", data: {json.dumps(delta_star_5)}, backgroundColor: "rgba(0, 122, 255, 0.85)" }},
      {{ label: "4★", data: {json.dumps(delta_star_4)}, backgroundColor: "rgba(52, 199, 89, 0.85)" }},
      {{ label: "3★", data: {json.dumps(delta_star_3)}, backgroundColor: "rgba(255, 204, 0, 0.85)" }},
      {{ label: "2★", data: {json.dumps(delta_star_2)}, backgroundColor: "rgba(255, 149, 0, 0.85)" }},
      {{ label: "1★", data: {json.dumps(delta_star_1)}, backgroundColor: "rgba(255, 59, 48, 0.85)" }},
    ],
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: true, position: "top" }} }},
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }} }},
      y: {{ stacked: true, beginAtZero: true }},
    }},
  }},
}});

// Weekly new ratings by star (stacked bar, full history)
new Chart(document.getElementById("weeklyRatingsByStar"), {{
  type: "bar",
  data: {{
    labels: {json.dumps(weekly_keys)},
    datasets: [
      {{ label: "5★", data: {json.dumps(weekly_star_5)}, backgroundColor: "rgba(0, 122, 255, 0.85)" }},
      {{ label: "4★", data: {json.dumps(weekly_star_4)}, backgroundColor: "rgba(52, 199, 89, 0.85)" }},
      {{ label: "3★", data: {json.dumps(weekly_star_3)}, backgroundColor: "rgba(255, 204, 0, 0.85)" }},
      {{ label: "2★", data: {json.dumps(weekly_star_2)}, backgroundColor: "rgba(255, 149, 0, 0.85)" }},
      {{ label: "1★", data: {json.dumps(weekly_star_1)}, backgroundColor: "rgba(255, 59, 48, 0.85)" }},
    ],
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: true, position: "top" }} }},
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }}, ticks: {{ maxTicksLimit: 12, autoSkip: true, maxRotation: 0 }} }},
      y: {{ stacked: true, beginAtZero: true }},
    }},
  }},
}});

// Daily new ratings total (line)
new Chart(document.getElementById("dailyRatingsTotal"), {{
  type: "line",
  data: {{
    labels: {json.dumps(delta_dates)},
    datasets: [{{
      data: {json.dumps(delta_totals)},
      borderColor: colors.blue,
      backgroundColor: colors.blueFill,
      fill: true,
      tension: 0.3,
      pointRadius: {json.dumps(3 if len(delta_dates) < 60 else 0)},
    }}],
  }},
  options: {{ ...defaultOpts, scales: {{ ...defaultOpts.scales, y: {{ beginAtZero: true }} }} }},
}});

// Reviews-only star distribution
new Chart(document.getElementById("starDist"), {{
  type: "bar",
  data: {{
    labels: ["1 Star", "2 Stars", "3 Stars", "4 Stars", "5 Stars"],
    datasets: [{{
      data: {json.dumps(star_dist)},
      backgroundColor: colors.bars,
      borderRadius: 6,
    }}],
  }},
  options: {{ ...defaultOpts, scales: {{ ...defaultOpts.scales, y: {{ beginAtZero: true }} }} }},
}});

// Monthly review count
new Chart(document.getElementById("monthlyReviews"), {{
  type: "bar",
  data: {{
    labels: {json.dumps(monthly_labels)},
    datasets: [{{
      data: {json.dumps(monthly_counts)},
      backgroundColor: colors.blue,
      borderRadius: 4,
    }}],
  }},
  options: {{ ...defaultOpts, scales: {{ ...defaultOpts.scales, y: {{ beginAtZero: true }} }} }},
}});

// Ratings by version (bar chart)
new Chart(document.getElementById("versionRatings"), {{
  type: "bar",
  data: {{
    labels: {json.dumps(version_labels)},
    datasets: [{{
      data: {json.dumps(version_ratings)},
      backgroundColor: colors.blue,
      borderRadius: 4,
    }}],
  }},
  options: {{
    ...defaultOpts,
    scales: {{ ...defaultOpts.scales, y: {{ beginAtZero: true }} }},
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          afterLabel: function(ctx) {{
            const periods = {json.dumps(version_periods)};
            return periods[ctx.dataIndex];
          }}
        }}
      }}
    }}
  }},
}});

// Current version ratings over time (with version labels)
const vTimelineDates = {json.dumps(version_timeline_dates)};
const vTimelineCounts = {json.dumps(version_timeline_counts)};
const vTimelineNames = {json.dumps(version_timeline_names)};

// Build version boundary annotations
const versionBoundaries = [];
let prevVersion = vTimelineNames[0];
for (let i = 1; i < vTimelineNames.length; i++) {{
  if (vTimelineNames[i] !== prevVersion) {{
    versionBoundaries.push({{ x: vTimelineDates[i], label: vTimelineNames[i] }});
    prevVersion = vTimelineNames[i];
  }}
}}

new Chart(document.getElementById("versionTimeline"), {{
  type: "line",
  data: {{
    labels: vTimelineDates,
    datasets: [{{
      data: vTimelineCounts,
      borderColor: colors.blue,
      backgroundColor: colors.blueFill,
      fill: true,
      tension: 0.3,
      pointRadius: {json.dumps(3 if len(version_timeline_dates) < 60 else 0)},
    }}],
  }},
  options: {{
    ...defaultOpts,
    scales: {{ ...defaultOpts.scales, y: {{ beginAtZero: true }} }},
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          afterLabel: function(ctx) {{
            return "Version: " + vTimelineNames[ctx.dataIndex];
          }}
        }}
      }}
    }}
  }},
}});

// Monthly average rating
new Chart(document.getElementById("monthlyAvg"), {{
  type: "line",
  data: {{
    labels: {json.dumps(monthly_labels)},
    datasets: [{{
      data: {json.dumps(monthly_avgs)},
      borderColor: colors.green,
      backgroundColor: colors.greenFill,
      fill: true,
      tension: 0.3,
      pointRadius: 3,
    }}],
  }},
  options: {{ ...defaultOpts, scales: {{ ...defaultOpts.scales, y: {{ min: 0, max: 5, ticks: {{ stepSize: 1 }} }} }} }},
}});
</script>
</body>
</html>"""

    with open(output_path, "w") as f:
        f.write(html)
    print(f"HTML report written to {output_path}")


def export_csv(conn: sqlite3.Connection, output_dir: str):
    """Export all tables to CSV files in the given directory."""
    os.makedirs(output_dir, exist_ok=True)

    exports = {
        "reviews.csv": (
            "SELECT id, rating, title, body, reviewer, territory, app_version, created_date, fetched_at "
            "FROM reviews ORDER BY created_date"
        ),
        "rating_snapshots.csv": (
            "SELECT snapshot_date, app_id, store_avg_rating, store_rating_count, "
            "store_rating_count_current_version, current_version, fetched_at "
            "FROM rating_snapshots ORDER BY snapshot_date"
        ),
        "star_distribution.csv": (
            "SELECT snapshot_date, app_id, star_1, star_2, star_3, star_4, star_5, fetched_at "
            "FROM star_distribution_snapshots ORDER BY snapshot_date"
        ),
        "daily_snapshots.csv": (
            "SELECT snapshot_date, app_id, avg_rating, total_count, "
            "star_1, star_2, star_3, star_4, star_5, new_today, avg_rating_today, computed_at "
            "FROM daily_snapshots ORDER BY snapshot_date"
        ),
    }

    for filename, query in exports.items():
        filepath = os.path.join(output_dir, filename)
        cursor = conn.execute(query)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(rows)

        print(f"  {filepath} ({len(rows):,} rows)")


def main():
    parser = argparse.ArgumentParser(description="App Store ratings report")
    parser.add_argument("--db", default="./reviews.db", help="Path to SQLite database")
    parser.add_argument("--html", metavar="FILE", help="Generate HTML report to FILE")
    parser.add_argument("--csv", metavar="DIR", help="Export all data as CSV files to DIR")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"Database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    conn = get_db(args.db)
    cli_report(conn)

    if args.html:
        generate_html(conn, args.html)

    if args.csv:
        print(f"Exporting CSV files to {args.csv}/")
        export_csv(conn, args.csv)

    conn.close()


if __name__ == "__main__":
    main()
