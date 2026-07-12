#!/usr/bin/env python3
"""Update data/android-ratings.csv (Play Store rating over time) from the Play
Console bulk "ratings" reports in Cloud Storage.

Same auth/bucket as android_installs.py (a service account with the "download
bulk reports" permission); reuses its GCS helpers. Extracts "Total Average
Rating" — the cumulative store score Google shows — per day.

    uv run android_ratings.py --bucket pubsite_prod_rev_XXXX --csv     # date,rating
    uv run android_ratings.py --bucket pubsite_prod_rev_XXXX           # write the CSV
"""
from __future__ import annotations

import csv
import io
import os

import click
import requests

from android_installs import GCS, DEFAULT_PACKAGE, _download, _token

RATINGS_CSV = "data/android-ratings.csv"
METRIC = "Total Average Rating"


def list_rating_files(bucket: str, package: str, token: str) -> list[str]:
    prefix = f"stats/ratings/ratings_{package}_"
    names, page = [], None
    while True:
        params = {"prefix": prefix}
        if page:
            params["pageToken"] = page
        r = requests.get(f"{GCS}/b/{bucket}/o", params=params,
                         headers={"Authorization": f"Bearer {token}"}, timeout=60)
        r.raise_for_status()
        data = r.json()
        names += [o["name"] for o in data.get("items", [])]
        page = data.get("nextPageToken")
        if not page:
            break
    return sorted(n for n in names if n.endswith("_overview.csv"))


def fetch_rating_series(bucket: str, package: str, credentials) -> dict[str, str]:
    token = _token(credentials)
    series: dict[str, str] = {}
    for name in list_rating_files(bucket, package, token):
        text = _download(bucket, name, token).decode("utf-16")
        for row in csv.DictReader(io.StringIO(text)):
            date = (row.get("Date") or "").strip()
            value = (row.get(METRIC) or "").strip()
            if date and value:
                series[date] = value
    return series


def upsert(series: dict[str, str], path: str = RATINGS_CSV) -> tuple[int, str]:
    rows: dict[str, str] = {}
    if os.path.exists(path):
        with open(path, newline="") as f:
            reader = csv.reader(f)
            next(reader, None)
            for r in reader:
                if r:
                    rows[r[0]] = r[1]
    rows.update(series)
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "rating"])
        for d in sorted(rows):
            w.writerow([d, rows[d]])
    dates = sorted(rows)
    return len(dates), (dates[-1] if dates else "")


@click.command()
@click.option("--bucket", required=True, help="GCS bucket, e.g. pubsite_prod_rev_XXXX")
@click.option("--package", default=DEFAULT_PACKAGE, show_default=True)
@click.option("--credentials", envvar="GOOGLE_APPLICATION_CREDENTIALS",
              help="Service-account JSON path (or GOOGLE_APPLICATION_CREDENTIALS).")
@click.option("--csv", "as_csv", is_flag=True, help="Print date,rating to stdout instead of writing.")
def main(bucket, package, credentials, as_csv):
    """Update data/android-ratings.csv (Total Average Rating over time)."""
    series = fetch_rating_series(bucket, package, credentials)
    if not series:
        raise SystemExit("No rating data found — check bucket/package/permissions.")
    if as_csv:
        for d in sorted(series):
            click.echo(f"{d},{series[d]}")
        return
    n, last = upsert(series)
    click.echo(f"Wrote {RATINGS_CSV}: {n} rows (latest {last})")


if __name__ == "__main__":
    main()
