ActivityWatch stats
===================

Collecting and analyzing stats about the ActivityWatch project.

Some of the data can be viewed at: https://activitywatch.net/stats/

There's also a related project for generating contributor stats in: https://github.com/ActivityWatch/contributor-stats/


## Data

All data is stored in the `data` folder.

These data are updated automatically in CI:

 - `stats.csv` - Downloads & GitHub stars
 - `releases.csv` - Release dates of past releases (GitHub *tags* — when a version was cut, not when it reached users)
 - `android-tracks.csv` - Play release-track state: which versionCode each track serves, and staged-rollout progress
 - `stats-assets.csv` - Per-asset download counts (source for per-platform / per-version breakdowns)
 - `android/installed.csv` - Android install base (Active Device Installs, Play Console bulk reports)
 - `android-crash-rate.csv` / `android-anr-rate.csv` - Android vitals (Play Developer Reporting API)
 - `android-errors/current.md` / `current.json` - Ranked error issues and comparisons; immutable JSON snapshots in `android-errors/history/`
 - `android-ratings.csv` - Android Play Store rating over time (Total Average Rating, bulk reports)

The following is manually updated:

 - `chrome-weekly-users.csv` - Chrome extension weekly active users
 - `firefox-daily-users.csv` - Firefox extension daily active users
 - `notes.csv` - Manual entries of major/interesting events


## Android vitals

`vitals.py` pulls Android vitals (crash rate, ANR rate) from the Google Play
Developer Reporting API. It works both as an ad-hoc CLI (run with your own
service-account key) and as a CSV collector for the `data` folder:

```sh
uv run vitals.py summary                              # latest crash + ANR rate
uv run vitals.py crash-rate --days 60                 # timeline to stdout
uv run vitals.py crash-rate --update data/android-crash-rate.csv  # upsert daily series (collector)
uv run vitals.py errors --limit 10 --stacktraces      # top crash clusters + sample stacktraces
uv run vitals.py crash-rate --dry-run                 # inspect the API request, no auth
```

Needs a Google Cloud service account granted "view app quality / Android
vitals" access in the Play Console; point at the key with `--credentials` or
`GOOGLE_APPLICATION_CREDENTIALS`. See the module docstring for setup.

### Error reports for Android release triage

The daily Play collector writes a seven-day, top-100 report to
`data/android-errors/current.md`. The Android release owner reads this alongside
`android-tracks.csv` and Play Console's per-version vitals before promoting a build:

```sh
uv run vitals.py errors --type all --days 7 --limit 100 --stacktraces --markdown
uv run vitals.py errors --type all --days 7 --limit 100 --stacktraces --json
uv run vitals.py errors --type all --days 7 --limit 100 --stacktraces --update-dir data/android-errors
```

Start with the highest report counts and increases, inspect the retained sample
frames, and link each actionable cluster to its existing `ActivityWatch/aw-android`
issue or open one with the report's stable ID and evidence. Counts are reports
within the recorded query window, not unique users or severity scores. Severity
is `unclassified` until triage. Confirm that an affected version reached users
using `android-tracks.csv`, then check Play Console's per-version vitals before
claiming a release fixed an issue. Samples describe observed reports; they cannot
prove coverage of every affected or fixed version.

JSON schema version 1 records `package`, `collected_at`, `query` (type, days,
limit, and hour-aligned UTC start/end), `coverage`, `clusters`, and `comparison`.
`--days` defaults to 1. Each cluster has an `identity`, `identity_source`,
`report_count`, sanitized `trace`, optional sample version/time provenance, and
optional disposition. Play's full issue resource name is the primary identity;
a deterministic heuristic fingerprint is used only when that name is absent.
Google warns that its [alpha issue grouping can change identities](https://developers.google.com/play/developer/reporting/reference/rest/v1beta1/vitals.errors.issues).

Raw `reportText`, exception messages, local paths, and credentials are excluded
from persisted reports. Structural sample frames and provenance are retained;
frame extraction is a heuristic because Google does not guarantee the
[report text's machine-readable format](https://developers.google.com/play/developer/reporting/reference/rest/v1beta1/vitals.errors.reports).

Comparisons require the same package, issue type, window duration, and result
limit. Daily seven-day windows overlap, so count changes compare rolling windows.
A missing issue is **not observed**, never automatically fixed: it may fall
outside the window or top-N limit, or Play may regroup it.

`--update-dir` writes `current.json`, `current.md`, and an append-only
`history/<timestamp>-<contenthash>.json` snapshot. API failures fail collection
instead of publishing an empty success. Maintain triage decisions separately in
`data/android-errors/dispositions.json` (automatically read if present), or pass
`--dispositions PATH`:

```json
{
  "apps/net.activitywatch.android/ISSUE_ID": {
    "status": "investigating",
    "issue_url": "https://github.com/ActivityWatch/aw-android/issues/210",
    "note": "Confirm affected version codes before release promotion."
  }
}
```

Use actual identities from the report. The collector never overwrites this file;
review and commit disposition changes manually. The workflow stages only the
generated current files and history directory, and removes its temporary
service-account key even when collection fails.

## Play release tracks

`releases.csv` records when a version was *tagged*, which is not when it
reached users — v0.14.0b2 was tagged 2026-07-22 and promoted to production a
month later. `play_tracks.py` records the other half: which release each Play
track currently serves, and how far a staged rollout has progressed.

```shell
uv run play_tracks.py                                  # human summary per track
uv run play_tracks.py --update data/android-tracks.csv # daily snapshot (idempotent)
uv run play_tracks.py --csv                            # rows to stdout
```

This uses the Android Publisher API (not the Reporting API `vitals.py` uses),
so the service account needs an app permission that allows opening an edit —
at least "Release to testing tracks". "View app quality information" alone is
not enough; the tool exits 3 with that advice rather than a stack trace.

Pair it with `vitals.py by-version` to attribute a crash rate to a release:
the app-wide rate is dominated by whatever the install base still runs, so a
partial rollout of a genuinely fixed build barely moves it.

`android_installs.py` automates `android/installed.csv` (the install base
currently exported by hand) from the Play Console bulk "installs" reports in
Cloud Storage:

```sh
uv run android_installs.py --bucket pubsite_prod_rev_XXXX list    # what's available
uv run android_installs.py --bucket pubsite_prod_rev_XXXX update  # write installed.csv
```

Needs a service account with the Play Console "Download bulk reports"
permission; get the bucket from Play Console -> Download reports -> Statistics
("Copy Cloud Storage URI").


### TODO

 - Twitter followers
 - AlternativeTo votes
 - ~~Events - releases, posts to reddit, published on ProductHunt, etc.~~
 - ~~Per version/platform download stats~~ (see `stats-assets.csv`)
 - Website analytics (collected with Google Analytics, might be analyzed here)
 - Android vitals collection in CI (once a service-account secret is provisioned)
