#!/usr/bin/env python3
"""Update data/android/installed.csv from Play Console bulk "installs" reports
in Google Cloud Storage — automating the currently-manual Statistics export.

Play Console writes monthly CSV reports to a private GCS bucket
(`gs://pubsite_prod_rev_<id>/`). This reads the per-app installs *overview*
reports and writes the daily install base into `installed.csv`, preserving the
pre-report history and the manual Notes column.

Usage
-----
    # inspect which report files exist (auth required, no writes)
    uv run android_installs.py --bucket pubsite_prod_rev_XXXX list

    # print the daily series as date,value (no writes)
    uv run android_installs.py --bucket pubsite_prod_rev_XXXX csv

    # update data/android/installed.csv in place (merges + keeps notes)
    uv run android_installs.py --bucket pubsite_prod_rev_XXXX update

Auth
----
A Google service account added as a Play Console user with the "Download bulk
reports (read-only)" permission, so it can read the reports bucket. Provide the
key via `--credentials` or `GOOGLE_APPLICATION_CREDENTIALS`. Find the bucket in
Play Console -> Download reports -> Statistics -> "Copy Cloud Storage URI"
(the `pubsite_prod_rev_...` part).

Note: the install-base column defaults to "Active Device Installs" (the bulk
report's equivalent of the Statistics "Installed audience"). Validate against
the current installed.csv on the first real run; override with --metric-column
if needed.
"""
from __future__ import annotations

import csv
import io
import os
from urllib.parse import quote

import click
import requests

SCOPE = "https://www.googleapis.com/auth/devstorage.read_only"
GCS = "https://storage.googleapis.com/storage/v1"
DEFAULT_PACKAGE = "net.activitywatch.android"
CANON = "data/android/installed.csv"
# Consistent key location shared by local runs, agents, and CI.
DEFAULT_CREDENTIALS = os.path.expanduser("~/.config/activitywatch/play-sa.json")
DEFAULT_METRIC_COLUMN = "Active Device Installs"
INSTALLED_HEADER = ["Date", "Active Device Installs", "Notes"]


def _token(credentials: str | None) -> str:
    import google.auth
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    credentials = credentials or (
        DEFAULT_CREDENTIALS if os.path.exists(DEFAULT_CREDENTIALS) else None
    )
    if credentials:
        creds = service_account.Credentials.from_service_account_file(
            credentials, scopes=[SCOPE]
        )
    else:
        creds, _ = google.auth.default(scopes=[SCOPE])
    creds.refresh(Request())
    return creds.token


def list_report_files(bucket: str, package: str, token: str) -> list[str]:
    """Names of the installs *overview* report objects for the app."""
    prefix = f"stats/installs/installs_{package}_"
    names, page = [], None
    while True:
        params = {"prefix": prefix}
        if page:
            params["pageToken"] = page
        r = requests.get(
            f"{GCS}/b/{bucket}/o",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        names += [o["name"] for o in data.get("items", [])]
        page = data.get("nextPageToken")
        if not page:
            break
    return sorted(n for n in names if n.endswith("_overview.csv"))


def _download(bucket: str, name: str, token: str) -> bytes:
    r = requests.get(
        f"{GCS}/b/{bucket}/o/{quote(name, safe='')}",
        params={"alt": "media"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )
    r.raise_for_status()
    return r.content


def parse_report(raw: bytes, metric_column: str) -> dict[str, str]:
    """Parse one bulk installs report (UTF-16) into {date: install_base}."""
    text = raw.decode("utf-16")  # Play Console bulk reports are UTF-16 (LE + BOM)
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        date = (row.get("Date") or "").strip()
        value = (row.get(metric_column) or "").strip().replace(",", "")
        # Skip "0": Play leaves not-yet-finalized recent days as 0, which would
        # otherwise be a spurious dip in the install base.
        if date and value and value != "0":
            out[date] = value
    return out


def fetch_install_series(bucket, package, metric_column, credentials) -> dict[str, str]:
    token = _token(credentials)
    series: dict[str, str] = {}
    for name in list_report_files(bucket, package, token):
        series.update(parse_report(_download(bucket, name, token), metric_column))
    return series


def merge_into_installed(series: dict[str, str], path: str = CANON) -> dict[str, list]:
    """Overlay the report's daily values onto installed.csv, keeping notes."""
    rows: dict[str, list] = {}
    try:
        with open(path, newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # header
            for r in reader:
                if r:
                    rows[r[0]] = [r[1], r[2] if len(r) > 2 else ""]
    except FileNotFoundError:
        pass
    for date, value in series.items():
        note = rows.get(date, ["", ""])[1]
        rows[date] = [value, note]
    return rows


def write_installed(rows: dict[str, list], path: str = CANON) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(INSTALLED_HEADER)
        for date in sorted(rows):
            w.writerow([date, rows[date][0], rows[date][1]])


# --- CLI -------------------------------------------------------------------


@click.group(help=__doc__)
@click.option("--bucket", required=True, help="GCS bucket, e.g. pubsite_prod_rev_XXXX")
@click.option("--package", default=DEFAULT_PACKAGE, show_default=True)
@click.option("--credentials", envvar="GOOGLE_APPLICATION_CREDENTIALS",
              help="Service-account JSON path (or GOOGLE_APPLICATION_CREDENTIALS).")
@click.option("--metric-column", default=DEFAULT_METRIC_COLUMN, show_default=True)
@click.pass_context
def cli(ctx, bucket, package, credentials, metric_column):
    ctx.obj = dict(bucket=bucket, package=package,
                   credentials=credentials, metric_column=metric_column)


@cli.command("list")
@click.pass_obj
def list_cmd(o):
    """List the installs report files available in the bucket."""
    for name in list_report_files(o["bucket"], o["package"], _token(o["credentials"])):
        click.echo(name)


@cli.command("csv")
@click.pass_obj
def csv_cmd(o):
    """Print the daily install base as date,value (no writes)."""
    series = fetch_install_series(o["bucket"], o["package"], o["metric_column"], o["credentials"])
    for date in sorted(series):
        click.echo(f"{date},{series[date]}")


@cli.command("update")
@click.option("--path", default=CANON, show_default=True)
@click.pass_obj
def update_cmd(o, path):
    """Update data/android/installed.csv in place (merge, keep notes)."""
    series = fetch_install_series(o["bucket"], o["package"], o["metric_column"], o["credentials"])
    if not series:
        raise SystemExit("No install data found — check bucket/package/permissions.")
    rows = merge_into_installed(series, path)
    write_installed(rows, path)
    dates = sorted(rows)
    click.echo(f"Wrote {path}: {len(dates)} rows, {dates[0]}..{dates[-1]} "
               f"(latest {rows[dates[-1]][0]})")


if __name__ == "__main__":
    cli()
