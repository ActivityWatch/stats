#!/usr/bin/env python3
"""Collect Play Store *release track* state for the ActivityWatch Android app.

Why this exists
---------------
`data/releases.csv` records **GitHub tags** — when a version was cut, not when
(or whether) it reached users. Those are different events: v0.14.0b2 was tagged
on 2026-07-22 and promoted to the production track a month later. Nothing in
this repo captured the promotion, so questions like

    "we pushed b2 to prod on Monday — is the rollout complete?"
    "which versionCode is production actually serving?"

were unanswerable from collected data, and answering them from `releases.csv`
gives a confidently wrong answer.

This tool records the missing half: for each Play track (production, beta,
alpha, internal), which release is live, its status, and the staged-rollout
fraction. Combined with `vitals.py by-version`, that makes a crash rate
attributable to a release instead of app-wide.

Usage
-----
    uv run play_tracks.py list                       # human table
    uv run play_tracks.py list --update data/android-tracks.csv

Auth
----
Same service-account key as `vitals.py` (`--credentials` /
`GOOGLE_APPLICATION_CREDENTIALS`), but a different API and scope: the Android
Publisher API, scope `.../auth/androidpublisher`. The account needs an app
permission that permits opening an edit (e.g. "Release to testing tracks");
"View app quality information" alone is *not* enough. A permission failure is
reported loudly and never written to CSV — see `_check_access`.
"""
from __future__ import annotations

import csv
import os
from datetime import date

import click
import requests

SCOPE = "https://www.googleapis.com/auth/androidpublisher"
BASE = "https://androidpublisher.googleapis.com/androidpublisher/v3"
DEFAULT_PACKAGE = "net.activitywatch.android"
DEFAULT_CREDENTIALS = os.path.expanduser("~/.config/activitywatch/play-sa.json")

CSV_HEADER = ["date", "track", "status", "user_fraction", "version_codes", "release_name"]

# Exit code for "authenticated fine, but this account may not read tracks".
# Distinct from 1 so a collector can tell a permission gap from a real failure.
EXIT_NO_ACCESS = 3


def _access_token(credentials: str | None) -> str:
    """OAuth token for the Android Publisher API from a service-account key."""
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


def _check_access(resp: requests.Response) -> None:
    """Turn a 401/403 into an actionable message instead of a stack trace.

    The common case is a service account provisioned for vitals only: it has
    "View app quality information" but not the release permission an edit
    needs. That reads as a hard failure unless we say what to grant.
    """
    if resp.status_code not in (401, 403):
        return
    click.echo(
        f"Play Publisher API denied access ({resp.status_code}).\n"
        "  The service account can reach the API but is not allowed to open an "
        "edit for this app.\n"
        "  Grant it in Play Console -> Users & permissions -> the SA's email ->\n"
        "  app permissions -> at least 'Release to testing tracks'.\n"
        "  ('View app quality information', which vitals.py uses, is not enough.)\n"
        f"  API said: {resp.text[:300]}",
        err=True,
    )
    raise SystemExit(EXIT_NO_ACCESS)


def fetch_tracks(package: str, credentials: str | None) -> list[dict]:
    """Return the current release state of every Play track.

    Opening an "edit" is how the Publisher API exposes track state; the edit is
    a scratch transaction, never committed, and deleted again below — so this
    is a read even though it takes a POST to start.
    """
    token = _access_token(credentials)
    headers = {"Authorization": f"Bearer {token}"}

    r = requests.post(f"{BASE}/applications/{package}/edits", headers=headers, timeout=30)
    _check_access(r)
    r.raise_for_status()
    edit_id = r.json()["id"]

    try:
        r = requests.get(
            f"{BASE}/applications/{package}/edits/{edit_id}/tracks",
            headers=headers,
            timeout=30,
        )
        _check_access(r)
        r.raise_for_status()
        return r.json().get("tracks", [])
    finally:
        # Best-effort cleanup: an abandoned edit expires on its own, and
        # failing to delete it must not lose the data we just read.
        requests.delete(
            f"{BASE}/applications/{package}/edits/{edit_id}", headers=headers, timeout=30
        )


def parse_tracks(tracks: list[dict], today: str) -> list[dict]:
    """Flatten the API shape into one row per (track, release).

    A track can carry more than one release at once — a staged rollout runs
    alongside the completed release it is replacing — so the rollout question
    is only answerable if both rows survive.
    """
    rows = []
    for track in tracks:
        name = track.get("track", "")
        for release in track.get("releases", []):
            codes = ";".join(str(c) for c in release.get("versionCodes") or [])
            fraction = release.get("userFraction")
            rows.append(
                {
                    "date": today,
                    "track": name,
                    "status": release.get("status", ""),
                    # A completed release serves everyone; the API omits
                    # userFraction there rather than sending 1.0.
                    "user_fraction": (
                        "1.0"
                        if fraction is None and release.get("status") == "completed"
                        else ("" if fraction is None else repr(float(fraction)))
                    ),
                    "version_codes": codes,
                    "release_name": release.get("name", ""),
                }
            )
    rows.sort(key=lambda r: (r["track"], r["release_name"], r["version_codes"]))
    return rows


def upsert_csv(path: str, rows: list[dict]) -> int:
    """Merge rows into the CSV keyed by (date, track, version_codes).

    Re-running on the same day overwrites that day's snapshot rather than
    appending a duplicate, so the daily collector is idempotent.
    """
    existing: dict[tuple[str, str, str], dict] = {}
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                existing[(r["date"], r["track"], r["version_codes"])] = r
    for row in rows:
        existing[(row["date"], row["track"], row["version_codes"])] = row
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADER, lineterminator="\n")
        w.writeheader()
        for key in sorted(existing):
            w.writerow({k: existing[key].get(k, "") for k in CSV_HEADER})
    return len(existing)


def rollout_summary(rows: list[dict]) -> list[str]:
    """One line per track answering 'is the rollout complete?'."""
    out = []
    for row in rows:
        codes = row["version_codes"] or "?"
        if row["status"] == "completed":
            state = "complete (100% of users)"
        elif row["status"] == "inProgress":
            frac = row["user_fraction"]
            pct = f"{float(frac) * 100:g}%" if frac else "unknown %"
            state = f"IN PROGRESS — staged at {pct}"
        elif row["status"] == "halted":
            state = "HALTED"
        else:
            state = row["status"] or "unknown"
        out.append(f"  {row['track']:<12} versionCode {codes:<10} {state}")
    return out


@click.command()
@click.option("--package", default=DEFAULT_PACKAGE, show_default=True)
@click.option(
    "--credentials",
    envvar="GOOGLE_APPLICATION_CREDENTIALS",
    help="Path to service-account JSON (or set GOOGLE_APPLICATION_CREDENTIALS).",
)
@click.option(
    "--update",
    "update_path",
    default=None,
    help="Upsert today's snapshot into this CSV (idempotent per day).",
)
@click.option("--csv", "as_csv", is_flag=True, help="Print CSV rows to stdout.")
def list_tracks(package, credentials, update_path, as_csv):
    """Show which release each Play track is currently serving."""
    tracks = fetch_tracks(package, credentials)
    rows = parse_tracks(tracks, date.today().isoformat())
    if not rows:
        raise SystemExit(f"No track releases returned for {package}")

    if as_csv:
        w = csv.DictWriter(click.get_text_stream("stdout"), fieldnames=CSV_HEADER,
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    else:
        click.echo(f"Play track state for {package} ({rows[0]['date']}):")
        for line in rollout_summary(rows):
            click.echo(line)

    if update_path:
        total = upsert_csv(update_path, rows)
        click.echo(f"Wrote {len(rows)} row(s) to {update_path} ({total} total)", err=True)


if __name__ == "__main__":
    list_tracks()
