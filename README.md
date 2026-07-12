ActivityWatch stats
===================

Collecting and analyzing stats about the ActivityWatch project.

Some of the data can be viewed at: https://activitywatch.net/stats/

There's also a related project for generating contributor stats in: https://github.com/ActivityWatch/contributor-stats/


## Data

All data is stored in the `data` folder.

These data are updated automatically in CI:

 - `stats.csv` - Downloads & GitHub stars
 - `releases.csv` - Release dates of past releases
 - `stats-assets.csv` - Per-asset download counts (source for per-platform / per-version breakdowns)

The following is manually updated:

 - `chrome-weekly-users.csv` - Chrome extension weekly active users
 - `firefox-daily-users.csv` - Firefox extension daily active users
 - `android/installed.csv` - Android installed devices (Play Console export)
 - `notes.csv` - Manual entries of major/interesting events


## Android vitals

`vitals.py` pulls Android vitals (crash rate, ANR rate) from the Google Play
Developer Reporting API. It works both as an ad-hoc CLI (run with your own
service-account key) and as a CSV collector for the `data` folder:

```sh
uv run vitals.py summary                              # latest crash + ANR rate
uv run vitals.py crash-rate --days 60                 # timeline to stdout
uv run vitals.py crash-rate --update data/android-crash-rate.csv  # upsert daily series (collector)
uv run vitals.py crash-rate --dry-run                 # inspect the API request, no auth
```

Needs a Google Cloud service account granted "view app quality / Android
vitals" access in the Play Console; point at the key with `--credentials` or
`GOOGLE_APPLICATION_CREDENTIALS`. See the module docstring for setup.

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
