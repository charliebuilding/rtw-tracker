#!/usr/bin/env python3
"""
RTW Tracker Updater
Downloads CSVs from Let's Do This, parses them, updates index.html, and pushes to git.

Usage:
  # With local CSV files:
  python update_tracker.py --startlist path/to/startlist.csv --application path/to/application.csv

  # With automatic download from LDT (requires LDT_SESSION_COOKIE env var):
  python update_tracker.py --download
"""

import csv
import re
import os
import sys
import subprocess
import argparse
import glob
from datetime import datetime, date
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
INDEX_HTML = SCRIPT_DIR / "index.html"
LAUNCH_DATE = "2026-01-18"


def find_latest_csv(pattern):
    """Find the most recent CSV matching a pattern in ~/Downloads."""
    downloads = Path.home() / "Downloads"
    matches = sorted(downloads.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def download_csvs(cookie):
    """Download STARTLIST and APPLICATION CSVs from Let's Do This."""
    import urllib.request

    base_url = "https://www.letsdothis.com/api/v4/dashboard"
    org_id = "171180"
    event_id = "248572"
    occ_id = "21111161678"

    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    }

    downloads = SCRIPT_DIR
    csvs = {}

    for export_type in ["STARTLIST", "APPLICATION"]:
        url = f"{base_url}/o/{org_id}/e/{event_id}/occ/{occ_id}/entries/export?type={export_type}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                filename = f"Run The Wharf 2026_All Races_{export_type}_{timestamp}.csv"
                filepath = downloads / filename
                with open(filepath, "wb") as f:
                    f.write(resp.read())
                csvs[export_type.lower()] = filepath
                print(f"  Downloaded {export_type} -> {filepath.name}")
        except Exception as e:
            print(f"  ERROR downloading {export_type}: {e}")
            print(f"  Falling back to latest local CSV...")
            pattern = f"Run The Wharf 2026_All Races_{export_type}_*.csv"
            local = find_latest_csv(pattern)
            if local:
                csvs[export_type.lower()] = local
                print(f"  Using local: {local.name}")
            else:
                print(f"  No local {export_type} CSV found!")
                sys.exit(1)

    return csvs.get("startlist"), csvs.get("application")


def parse_startlist(csv_path):
    """Parse STARTLIST CSV and return sales counts by date."""
    sales_by_date = defaultdict(int)
    total_rows = 0

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            booking_date = row.get("Booking Date", "").strip()
            if not booking_date:
                continue
            # Normalise date format to YYYY-MM-DD
            try:
                dt = datetime.strptime(booking_date, "%Y-%m-%d")
                sales_by_date[dt.strftime("%Y-%m-%d")] += 1
                total_rows += 1
            except ValueError:
                # Try other date formats
                for fmt in ["%d/%m/%Y", "%m/%d/%Y"]:
                    try:
                        dt = datetime.strptime(booking_date, fmt)
                        sales_by_date[dt.strftime("%Y-%m-%d")] += 1
                        total_rows += 1
                        break
                    except ValueError:
                        continue

    # Split into early bird (before launch) and post-launch
    launch = datetime.strptime(LAUNCH_DATE, "%Y-%m-%d")
    early_bird = 0
    post_launch = {}

    for d, count in sorted(sales_by_date.items()):
        dt = datetime.strptime(d, "%Y-%m-%d")
        if dt < launch:
            early_bird += count
        else:
            post_launch[d] = count

    print(f"  Parsed {total_rows} entries from STARTLIST")
    print(f"  Early bird (pre-launch): {early_bird}")
    print(f"  Post-launch dates: {len(post_launch)}")
    print(f"  Post-launch total: {sum(post_launch.values())}")

    return early_bird, post_launch


def parse_revenue(value):
    """Parse revenue string like '£562.50' or '£3,050' into float."""
    cleaned = re.sub(r"[£$,\s]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_application(csv_path):
    """Parse APPLICATION CSV and return corporate bookings aggregated by company."""
    companies = defaultdict(lambda: {"tickets": 0, "revenue": 0.0, "date": None})

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            company = row.get("Company Name", "").strip()
            if not company:
                continue

            tickets = int(row.get("Tickets", 0))
            revenue = parse_revenue(row.get("Total paid", "0"))
            app_date = row.get("Application Date", "").strip()

            # Normalise date
            try:
                dt = datetime.strptime(app_date, "%Y-%m-%d")
                app_date = dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

            companies[company]["tickets"] += tickets
            companies[company]["revenue"] += revenue
            # Keep the earliest application date
            if companies[company]["date"] is None or app_date < companies[company]["date"]:
                companies[company]["date"] = app_date

    # Sort by tickets descending
    bookings = []
    for company, data in sorted(companies.items(), key=lambda x: x[1]["tickets"], reverse=True):
        bookings.append({
            "company": company,
            "tickets": data["tickets"],
            "revenue": round(data["revenue"]),
            "date": data["date"],
            "onStartlist": 0,  # Will be preserved from existing data where possible
        })

    print(f"  Parsed {len(bookings)} companies from APPLICATION")
    for b in bookings:
        print(f"    {b['company']}: {b['tickets']} tickets, £{b['revenue']}")

    return bookings


def get_existing_on_startlist(html_content):
    """Extract existing onStartlist values from the current HTML."""
    pattern = r"company:\s*'([^']+)'.*?onStartlist:\s*(\d+)"
    existing = {}
    for match in re.finditer(pattern, html_content, re.DOTALL):
        existing[match.group(1)] = int(match.group(2))
    return existing


def format_sales_by_date(post_launch_sales):
    """Format salesByDate as a JS object string."""
    lines = []
    for d in sorted(post_launch_sales.keys()):
        lines.append(f"            '{d}': {post_launch_sales[d]},")
    return "\n".join(lines)


def format_corporate_bookings(bookings, existing_on_startlist):
    """Format corporateBookings as a JS array string."""
    lines = []
    for b in bookings:
        # Preserve existing onStartlist values
        on_startlist = existing_on_startlist.get(b["company"], b["onStartlist"])
        parts = [
            f"company: '{b['company']}'",
            f"tickets: {b['tickets']}",
            f"revenue: {b['revenue']}",
            f"date: '{b['date']}'",
            f"onStartlist: {on_startlist}",
        ]
        lines.append(f"            {{ {', '.join(parts)} }},")
    return "\n".join(lines)


def update_html(early_bird, post_launch_sales, corporate_bookings, data_date):
    """Update index.html with new data."""
    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()

    # Preserve existing onStartlist values
    existing_on_startlist = get_existing_on_startlist(html)

    # 1. Update dataDate
    html = re.sub(
        r"dataDate:\s*'[^']*'",
        f"dataDate: '{data_date}'",
        html
    )

    # 2. Update earlyBirdTotal
    html = re.sub(
        r"earlyBirdTotal:\s*\d+",
        f"earlyBirdTotal: {early_bird}",
        html
    )

    # 3. Replace salesByDate object
    sales_js = format_sales_by_date(post_launch_sales)
    html = re.sub(
        r"(const salesByDate = \{)\n.*?(\n        \};)",
        f"\\1\n{sales_js}\\2" if sales_js else "\\1\\2",
        html,
        flags=re.DOTALL,
    )

    # 4. Replace corporateBookings array
    corp_js = format_corporate_bookings(corporate_bookings, existing_on_startlist)
    html = re.sub(
        r"(const corporateBookings = \[)\n.*?(\n        \];)",
        f"\\1\n{corp_js}\\2" if corp_js else "\\1\\2",
        html,
        flags=re.DOTALL,
    )

    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  Updated {INDEX_HTML}")


def git_commit_and_push(data_date):
    """Commit changes and push to remote."""
    os.chdir(SCRIPT_DIR)

    # Check for changes
    result = subprocess.run(["git", "diff", "--quiet", "index.html"], capture_output=True)
    if result.returncode == 0:
        print("  No changes to commit.")
        return False

    subprocess.run(["git", "add", "index.html"], check=True)
    msg = f"Update tracker data ({data_date})"
    subprocess.run(["git", "commit", "-m", msg], check=True)

    # Push if remote exists
    result = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True)
    if result.returncode == 0:
        subprocess.run(["git", "push", "-u", "origin", "main"], check=True)
        print("  Pushed to remote.")
    else:
        print("  No remote configured - skipping push.")

    return True


def main():
    parser = argparse.ArgumentParser(description="Update RTW Tracker from Let's Do This CSVs")
    parser.add_argument("--startlist", help="Path to STARTLIST CSV file")
    parser.add_argument("--application", help="Path to APPLICATION CSV file")
    parser.add_argument("--download", action="store_true", help="Download CSVs from LDT (requires LDT_SESSION_COOKIE env var)")
    parser.add_argument("--no-push", action="store_true", help="Skip git commit and push")
    args = parser.parse_args()

    print("RTW Tracker Updater")
    print("=" * 40)

    # Resolve CSV paths
    startlist_path = None
    application_path = None

    if args.download:
        cookie = os.environ.get("LDT_SESSION_COOKIE")
        if not cookie:
            print("ERROR: Set LDT_SESSION_COOKIE environment variable")
            print("  Get it from browser DevTools > Application > Cookies > letsdothis.com")
            sys.exit(1)
        print("\n1. Downloading CSVs from Let's Do This...")
        startlist_path, application_path = download_csvs(cookie)
    else:
        if args.startlist:
            startlist_path = Path(args.startlist)
        else:
            # Auto-find latest in Downloads
            startlist_path = find_latest_csv("Run The Wharf 2026_All Races_STARTLIST_*.csv")
            if startlist_path:
                print(f"\n  Auto-found STARTLIST: {startlist_path.name}")

        if args.application:
            application_path = Path(args.application)
        else:
            application_path = find_latest_csv("Run The Wharf 2026_All Races_APPLICATION_*.csv")
            if application_path:
                print(f"  Auto-found APPLICATION: {application_path.name}")

    if not startlist_path or not startlist_path.exists():
        print("ERROR: No STARTLIST CSV found. Provide --startlist or place in ~/Downloads")
        sys.exit(1)
    if not application_path or not application_path.exists():
        print("ERROR: No APPLICATION CSV found. Provide --application or place in ~/Downloads")
        sys.exit(1)

    # Parse CSVs
    print("\n2. Parsing STARTLIST...")
    early_bird, post_launch_sales = parse_startlist(startlist_path)

    print("\n3. Parsing APPLICATION...")
    corporate_bookings = parse_application(application_path)

    # Determine data date (latest date in the sales data) + current time
    all_dates = list(post_launch_sales.keys())
    latest_date = max(all_dates) if all_dates else date.today().isoformat()
    now = datetime.now()
    data_date = f"{latest_date}T{now.strftime('%H:%M')}"

    # Update HTML
    print(f"\n4. Updating index.html (data date: {data_date}, updated: {now.strftime('%H:%M')})...")
    update_html(early_bird, post_launch_sales, corporate_bookings, data_date)

    # Git
    if not args.no_push:
        print("\n5. Committing and pushing...")
        git_commit_and_push(data_date)
    else:
        print("\n5. Skipping git (--no-push)")

    print("\nDone!")


if __name__ == "__main__":
    main()
