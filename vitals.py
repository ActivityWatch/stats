#!/usr/bin/env python3
"""Pull Android vitals (crash rate, ANR rate) from the Google Play Developer
Reporting API for the ActivityWatch Android app.

One tool, two shapes of usage:

  * Ad-hoc / interactive (e.g. an agent with its own service account):
        uv run vitals.py summary
        uv run vitals.py crash-rate --days 60

  * Collector for the stats repo (append a daily time series, like
    collect_stats.py):
        uv run vitals.py crash-rate --csv >> data/android-crash-rate.csv
        uv run vitals.py anr-rate   --csv >> data/android-anr-rate.csv

Auth
----
Needs a Google Cloud *service account* that has been granted access to the
Play Console developer account:

  Play Console -> Users & permissions -> invite the service account's email,
  with at least "View app quality information (including Android vitals)";
  or link the SA's Cloud project under Play Console -> Setup -> API access.

Point the tool at the key with `--credentials PATH` or the
`GOOGLE_APPLICATION_CREDENTIALS` env var. Nothing is stored by this tool — the
key stays with whoever runs it (an agent's own SA locally, or a CI secret
written to a file for the run).

Note: metric names follow the Play Developer Reporting API. Defaults are the
user-perceived daily rates; override with `--metric` if the API surface
differs for this app (e.g. a *_28dUserWeighted variant to match the number
shown in the Play Console headline).
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import click
import requests

SCOPE = "https://www.googleapis.com/auth/playdeveloperreporting"
BASE = "https://playdeveloperreporting.googleapis.com/v1beta1"
DEFAULT_PACKAGE = "net.activitywatch.android"

# command -> (metric-set resource, default metric, human label)
METRIC_SETS = {
    "crash-rate": ("crashRateMetricSet", "userPerceivedCrashRate", "User-perceived crash rate"),
    "anr-rate": ("anrRateMetricSet", "userPerceivedAnrRate", "User-perceived ANR rate"),
}


def _access_token(credentials: str | None) -> str:
    """OAuth token for the Reporting API from a service account key (or ADC)."""
    import google.auth
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    if credentials:
        creds = service_account.Credentials.from_service_account_file(
            credentials, scopes=[SCOPE]
        )
    else:
        creds, _ = google.auth.default(scopes=[SCOPE])
    creds.refresh(Request())
    return creds.token


def _build_request(package: str, metric_set: str, metric: str, days: int) -> tuple[str, dict]:
    end = date.today()
    start = end - timedelta(days=days)
    url = f"{BASE}/apps/{package}/{metric_set}:query"
    body = {
        "timelineSpec": {
            "aggregationPeriod": "DAILY",
            "startTime": {"year": start.year, "month": start.month, "day": start.day},
            "endTime": {"year": end.year, "month": end.month, "day": end.day},
        },
        "metrics": [metric],
    }
    return url, body


def _parse_rows(rows: list, metric: str) -> list[tuple[str, float | None]]:
    series = []
    for row in rows:
        t = row.get("startTime", {})
        iso = f"{t.get('year', 0):04d}-{t.get('month', 0):02d}-{t.get('day', 0):02d}"
        value = None
        for m in row.get("metrics", []):
            if m.get("metric") == metric:
                raw = (m.get("decimalValue") or {}).get("value")
                value = float(raw) if raw is not None else None
        series.append((iso, value))
    series.sort()
    return series


def fetch_metric(package, metric_set, metric, days, credentials):
    url, body = _build_request(package, metric_set, metric, days)
    token = _access_token(credentials)
    headers = {"Authorization": f"Bearer {token}"}
    rows, page_token = [], None
    while True:
        payload = dict(body, **({"pageToken": page_token} if page_token else {}))
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        rows.extend(data.get("rows", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return _parse_rows(rows, metric)


# --- CLI -------------------------------------------------------------------

_common = [
    click.option("--package", default=DEFAULT_PACKAGE, show_default=True),
    click.option("--credentials", envvar="GOOGLE_APPLICATION_CREDENTIALS",
                 help="Path to service-account JSON (or set GOOGLE_APPLICATION_CREDENTIALS)."),
    click.option("--days", default=30, show_default=True, help="Days of history to fetch."),
    click.option("--metric", default=None, help="Override the metric name."),
    click.option("--csv", "as_csv", is_flag=True, help="Emit 'date,value' rows for a data file."),
    click.option("--dry-run", is_flag=True, help="Print the API request instead of sending it."),
]


def _with_common(f):
    for opt in reversed(_common):
        f = opt(f)
    return f


def _emit(metric_set, metric, label, package, days, credentials, as_csv, dry_run):
    if dry_run:
        url, body = _build_request(package, metric_set, metric, days)
        import json
        click.echo(f"POST {url}")
        click.echo(json.dumps(body, indent=2))
        return
    series = fetch_metric(package, metric_set, metric, days, credentials)
    if as_csv:
        for iso, val in series:
            if val is not None:
                click.echo(f"{iso},{val}")
        return
    if not series:
        click.echo("No data returned.")
        return
    latest_iso, latest = series[-1]
    click.echo(f"{label} ({metric}) for {package}")
    click.echo(f"  latest: {latest_iso}  {latest:.4%}" if latest is not None else "  latest: n/a")
    recent = [v for _, v in series[-7:] if v is not None]
    if recent:
        click.echo(f"  7-day avg: {sum(recent) / len(recent):.4%}")


@click.group(help=__doc__)
def cli():
    pass


@cli.command("crash-rate")
@_with_common
def crash_rate(package, credentials, days, metric, as_csv, dry_run):
    """User-perceived crash rate over time."""
    ms, default_metric, label = METRIC_SETS["crash-rate"]
    _emit(ms, metric or default_metric, label, package, days, credentials, as_csv, dry_run)


@cli.command("anr-rate")
@_with_common
def anr_rate(package, credentials, days, metric, as_csv, dry_run):
    """User-perceived ANR rate over time."""
    ms, default_metric, label = METRIC_SETS["anr-rate"]
    _emit(ms, metric or default_metric, label, package, days, credentials, as_csv, dry_run)


@cli.command("summary")
@click.option("--package", default=DEFAULT_PACKAGE, show_default=True)
@click.option("--credentials", envvar="GOOGLE_APPLICATION_CREDENTIALS")
@click.option("--days", default=30, show_default=True)
def summary(package, credentials, days):
    """Latest crash and ANR rate at a glance."""
    for key in ("crash-rate", "anr-rate"):
        ms, metric, label = METRIC_SETS[key]
        _emit(ms, metric, label, package, days, credentials, as_csv=False, dry_run=False)


if __name__ == "__main__":
    cli()
