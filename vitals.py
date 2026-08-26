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

import csv
import os
from datetime import date, timedelta

import click
import requests

SCOPE = "https://www.googleapis.com/auth/playdeveloperreporting"
BASE = "https://playdeveloperreporting.googleapis.com/v1beta1"
DEFAULT_PACKAGE = "net.activitywatch.android"
# Consistent key location shared by local runs, agents, and CI.
DEFAULT_CREDENTIALS = os.path.expanduser("~/.config/activitywatch/play-sa.json")

# Days behind "today" past which the API's DAILY feed counts as stalled
# rather than normally lagging.
STALE_FRESHNESS_DAYS = 3

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


def _datetime(d: date, tz: dict | None) -> dict:
    out: dict = {"year": d.year, "month": d.month, "day": d.day}
    if tz:
        out["timeZone"] = tz
    return out


def _build_body(end_dt: dict, days: int, metric: str,
                dimensions: tuple[str, ...] = ()) -> dict:
    """Build a query body for the DAILY window ending at `end_dt` (a DateTime).

    `dimensions` breaks the metric down instead of returning one app-wide
    number per day — `versionCode` is the one that answers "is the release we
    just shipped better?", which the aggregate cannot (see `by-version`).
    """
    end = date(end_dt["year"], end_dt["month"], end_dt["day"])
    tz = end_dt.get("timeZone")
    start = end - timedelta(days=days)
    body: dict = {
        "timelineSpec": {
            "aggregationPeriod": "DAILY",
            "startTime": _datetime(start, tz),
            "endTime": _datetime(end, tz),
        },
        "metrics": [metric],
    }
    if dimensions:
        body["dimensions"] = list(dimensions)
    return body


def _daily_freshness(base: str, metric_set: str, token: str) -> dict | None:
    """Latest available DAILY end date (a DateTime with its timeZone).

    The API rejects queries whose endTime is past this, and DAILY data is
    reported in a specific timezone (e.g. America/Los_Angeles), so we anchor
    the query to it rather than guessing 'today'.
    """
    r = requests.get(
        f"{base}/{metric_set}", headers={"Authorization": f"Bearer {token}"}, timeout=30
    )
    r.raise_for_status()
    for fr in r.json().get("freshnessInfo", {}).get("freshnesses", []):
        if fr.get("aggregationPeriod") == "DAILY":
            return fr.get("latestEndTime")
    return None


def _dimension_value(row: dict, dimension: str) -> str | None:
    """The row's value for `dimension`, as a display string.

    The API returns the value under one of several typed keys and may add a
    human `valueLabel` (e.g. a version *name* next to a versionCode), so we
    prefer the label when present and fall back to the raw value.
    """
    for d in row.get("dimensions", []):
        if d.get("dimension") != dimension:
            continue
        label = d.get("valueLabel")
        for key in ("stringValue", "int64Value", "value"):
            if d.get(key) is not None:
                raw = str(d[key])
                return f"{raw} ({label})" if label and label != raw else raw
        return str(label) if label else None
    return None


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


def fetch_rows(package, metric_set, metric, days, credentials,
               dimensions: tuple[str, ...] = ()):
    """Raw rows plus the API's DAILY freshness date, for dimensioned queries."""
    token = _access_token(credentials)
    base = f"{BASE}/apps/{package}"
    end_dt = _daily_freshness(base, metric_set, token)
    if not end_dt:
        raise SystemExit(f"No DAILY freshness available for {metric_set}")
    body = _build_body(end_dt, days, metric, dimensions)
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{base}/{metric_set}:query"
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
    return rows, end_dt


def _freshness_warning(end_dt: dict) -> str | None:
    """Warn when the API's own latest DAILY date has fallen behind.

    A stalled feed is otherwise invisible: the collector still exits 0 and
    re-writes the same rows, so `git diff --quiet` skips the commit and the
    daily job keeps reporting success while the series silently stops. Callers
    print this so a stall shows up as output rather than as absence of it.
    """
    try:
        latest = date(end_dt["year"], end_dt["month"], end_dt["day"])
    except (KeyError, TypeError, ValueError):
        return None
    lag = (date.today() - latest).days
    # Play's DAILY vitals normally land within ~2-3 days; beyond that the feed
    # is stalled, not merely lagging.
    if lag > STALE_FRESHNESS_DAYS:
        return (f"WARNING: latest available DAILY data is {latest} ({lag} days "
                f"behind today) — the feed is stalled, not just lagging.")
    return None


def fetch_metric(package, metric_set, metric, days, credentials):
    """The app-wide daily series, plus the API's latest available DAILY date."""
    rows, end_dt = fetch_rows(package, metric_set, metric, days, credentials)
    return _parse_rows(rows, metric), end_dt


# --- CLI -------------------------------------------------------------------

_common = [
    click.option("--package", default=DEFAULT_PACKAGE, show_default=True),
    click.option("--credentials", envvar="GOOGLE_APPLICATION_CREDENTIALS",
                 help="Path to service-account JSON (or set GOOGLE_APPLICATION_CREDENTIALS)."),
    click.option("--days", default=30, show_default=True, help="Days of history to fetch."),
    click.option("--metric", default=None, help="Override the metric name."),
    click.option("--csv", "as_csv", is_flag=True,
                 help="Print 'date,value' rows to stdout (one-shot; use --update to collect)."),
    click.option("--update", "update_path", default=None,
                 help="Upsert the series into this CSV by date (append-only collector, no dupes)."),
    click.option("--dry-run", is_flag=True, help="Print the API request instead of sending it."),
]


def _upsert_csv(path: str, series: list, value_header: str) -> tuple[int, str]:
    """Merge (date, value) series into a two-column CSV, keyed by date."""
    rows: dict[str, str] = {}
    if os.path.exists(path):
        with open(path, newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # header
            for r in reader:
                if r:
                    rows[r[0]] = r[1]
    for iso, val in series:
        if val is not None:
            rows[iso] = repr(val) if isinstance(val, float) else str(val)
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", value_header])
        for d in sorted(rows):
            w.writerow([d, rows[d]])
    dates = sorted(rows)
    return len(dates), (dates[-1] if dates else "")


def _with_common(f):
    for opt in reversed(_common):
        f = opt(f)
    return f


def _emit(metric_set, metric, label, package, days, credentials, as_csv, dry_run,
          update_path=None):
    if dry_run:
        import json
        today = date.today()
        # Live requests anchor endTime to the API's freshness date; here we
        # just preview the shape with today's date.
        body = _build_body({"year": today.year, "month": today.month, "day": today.day},
                           days, metric)
        click.echo(f"POST {BASE}/apps/{package}/{metric_set}:query")
        click.echo(json.dumps(body, indent=2))
        return
    series, end_dt = fetch_metric(package, metric_set, metric, days, credentials)
    warning = _freshness_warning(end_dt)
    if warning:
        click.echo(warning, err=True)
    if update_path:
        n, last = _upsert_csv(update_path, series, metric)
        click.echo(f"{update_path}: {n} rows (latest {last})")
        return
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
def crash_rate(package, credentials, days, metric, as_csv, update_path, dry_run):
    """User-perceived crash rate over time."""
    ms, default_metric, label = METRIC_SETS["crash-rate"]
    _emit(ms, metric or default_metric, label, package, days, credentials, as_csv, dry_run,
          update_path)


@cli.command("anr-rate")
@_with_common
def anr_rate(package, credentials, days, metric, as_csv, update_path, dry_run):
    """User-perceived ANR rate over time."""
    ms, default_metric, label = METRIC_SETS["anr-rate"]
    _emit(ms, metric or default_metric, label, package, days, credentials, as_csv, dry_run,
          update_path)


def fetch_error_issues(package, issue_type, limit, credentials):
    """Top error clusters (crash/ANR) with cause, location, and report count."""
    token = _access_token(credentials)
    base = f"{BASE}/apps/{package}"
    params = {"pageSize": limit}
    if issue_type and issue_type != "all":
        params["filter"] = f"errorIssueType = {issue_type.upper()}"
    r = requests.get(f"{base}/errorIssues:search",
                     headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("errorIssues", []), token


def fetch_sample_stacktrace(package, issue_id, token):
    base = f"{BASE}/apps/{package}"
    r = requests.get(f"{base}/errorReports:search",
                     headers={"Authorization": f"Bearer {token}"},
                     params={"pageSize": 1, "filter": f"errorIssueId = {issue_id}"}, timeout=30)
    r.raise_for_status()
    reports = r.json().get("errorReports", [])
    return reports[0].get("reportText", "") if reports else ""


@cli.command("errors")
@click.option("--package", default=DEFAULT_PACKAGE, show_default=True)
@click.option("--credentials", envvar="GOOGLE_APPLICATION_CREDENTIALS")
@click.option("--type", "issue_type", default="crash",
              type=click.Choice(["crash", "anr", "all"]), show_default=True)
@click.option("--limit", default=10, show_default=True)
@click.option("--stacktraces", is_flag=True, help="Include a sample stacktrace per cluster.")
def errors(package, credentials, issue_type, limit, stacktraces):
    """Top crash/ANR clusters (cause, location, report count) from Android vitals."""
    issues, token = fetch_error_issues(package, issue_type, limit, credentials)
    if not issues:
        click.echo("No error issues found.")
        return
    for i, iss in enumerate(issues, 1):
        click.echo(f"{i}. [{iss.get('type')}] {iss.get('errorReportCount', '?')} reports  "
                   f"{iss.get('location', '')}")
        click.echo(f"   {iss.get('cause', '')}")
        if stacktraces:
            st = fetch_sample_stacktrace(package, iss["name"].split("/")[-1], token)
            for line in st.splitlines()[:8]:
                click.echo(f"     {line}")
        click.echo()


@cli.command("by-version")
@click.option("--package", default=DEFAULT_PACKAGE, show_default=True)
@click.option("--credentials", envvar="GOOGLE_APPLICATION_CREDENTIALS")
@click.option("--days", default=14, show_default=True,
              help="Days of history to compare over.")
@click.option("--kind", type=click.Choice(["crash-rate", "anr-rate"]),
              default="crash-rate", show_default=True)
@click.option("--metric", default=None, help="Override the metric name.")
@click.option("--as-csv", "as_csv", is_flag=True,
              help="Print 'version,days,mean' rows instead of a table.")
@click.option("--dry-run", is_flag=True, help="Print the API request instead of sending it.")
def by_version(package, credentials, days, kind, metric, as_csv, dry_run):
    """Crash/ANR rate broken down by app version.

    The app-wide series in `crash-rate` cannot answer "is the release we just
    shipped better?" — a partial rollout is swamped by the install base still
    on the old build, so a genuinely fixed version barely moves the aggregate
    for weeks. Breaking the same metric down by `versionCode` compares the
    versions directly, which is the question people actually mean.
    """
    metric_set, default_metric, label = METRIC_SETS[kind]
    metric = metric or default_metric

    if dry_run:
        import json
        today = date.today()
        body = _build_body({"year": today.year, "month": today.month, "day": today.day},
                           days, metric, ("versionCode",))
        click.echo(f"POST {BASE}/apps/{package}/{metric_set}:query")
        click.echo(json.dumps(body, indent=2))
        return

    rows, end_dt = fetch_rows(package, metric_set, metric, days, credentials,
                              dimensions=("versionCode",))
    warning = _freshness_warning(end_dt)
    if warning:
        click.echo(warning, err=True)

    # version -> observed daily values (None means "no data that day")
    per_version: dict[str, list[float]] = {}
    for row in rows:
        version = _dimension_value(row, "versionCode")
        if version is None:
            continue
        for _, value in _parse_rows([row], metric):
            if value is not None:
                per_version.setdefault(version, []).append(value)

    if not per_version:
        click.echo("No per-version data returned.")
        return

    # Newest versionCode last: sort numerically when we can, else lexically.
    def _sort_key(v: str):
        head = v.split(" ", 1)[0]
        return (0, int(head)) if head.isdigit() else (1, 0)

    summary_rows = [
        (version, len(values), sum(values) / len(values))
        for version, values in sorted(per_version.items(), key=lambda kv: _sort_key(kv[0]))
    ]

    if as_csv:
        click.echo("version,days,mean")
        for version, n, mean in summary_rows:
            click.echo(f"{version},{n},{mean!r}")
        return

    click.echo(f"{label} ({metric}) by version for {package}, last {days} days")
    width = max(len(v) for v, _, _ in summary_rows)
    for version, n, mean in summary_rows:
        click.echo(f"  {version:<{width}}  {mean:>8.4%}  ({n} day(s) of data)")


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
