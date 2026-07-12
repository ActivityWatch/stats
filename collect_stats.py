import csv
import re
from pprint import pprint
import sys
import os
from datetime import datetime, timezone, date

import requests

ASSETS_CSV = "data/stats-assets.csv"
ASSET_FIELDS = ["timestamp", "tag", "asset", "platform", "downloads"]
# per_page maxes at 100; github_get_all() follows pagination beyond that.
RELEASES_PATH = "/repos/ActivityWatch/activitywatch/releases?per_page=100"


def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_get(path: str):
    response = requests.get(
        f"https://api.github.com{path}", headers=_github_headers(), timeout=30
    )
    response.raise_for_status()
    return response.json()


def github_get_all(path: str) -> list:
    """
    GET every page of a paginated GitHub list endpoint, following the `Link`
    headers. Needed because per_page caps at 100, so once there are >100
    releases a single request would silently truncate the list.
    """
    items: list = []
    url: str | None = f"https://api.github.com{path}"
    while url:
        response = requests.get(url, headers=_github_headers(), timeout=30)
        response.raise_for_status()
        items.extend(response.json())
        url = response.links.get("next", {}).get("url")
    return items


def platform(asset_name: str) -> str:
    """Infer the OS/platform from a release asset filename."""
    m = re.search(r"(macos|darwin|linux|windows)", asset_name)
    if not m:
        return "unknown"
    # Older assets used "darwin" rather than "macos" for the same platform.
    return "macos" if m.group(1) == "darwin" else m.group(1)


def downloads_by_asset() -> list[dict]:
    """
    Per-asset download counts across all releases.

    Returns a list of {tag, asset, platform, downloads} dicts. This is the raw
    source data; platform totals and per-version-per-platform breakdowns are
    aggregations of it (see analyze_stats.py).
    """
    d = github_get_all(RELEASES_PATH)
    rows = []
    for release in d:
        tag = release["tag_name"]
        for asset in release["assets"]:
            rows.append(
                {
                    "tag": tag,
                    "asset": asset["name"],
                    "platform": platform(asset["name"]),
                    "downloads": asset["download_count"],
                }
            )
    return rows


def downloads(verbose=False) -> int:
    """Total downloads across all release assets."""
    total = 0
    for r in downloads_by_asset():
        if verbose:
            print(f' - {r["tag"]} [{r["platform"]}] {r["asset"]}: {r["downloads"]}')
        total += r["downloads"]
    return total


def load_asset_counts(path: str) -> dict[tuple[str, str], int]:
    """Last-known download count per (tag, asset) from the assets CSV."""
    last: dict[tuple[str, str], int] = {}
    if not os.path.exists(path):
        return last
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            last[(row["tag"], row["asset"])] = int(row["downloads"])
    return last


def stars():
    d = github_get("/repos/ActivityWatch/activitywatch")
    if "stargazers_count" not in d:
        raise RuntimeError(
            f"GitHub API did not return stargazers_count: {d.get('message', 'unknown response')}"
        )
    return d["stargazers_count"]


def clones():
    # TODO: Needs push access to the repository
    d = github_get("/repos/ActivityWatch/activitywatch/traffic/clones?per=day")
    pprint(d)


def twitter():
    # TODO: Needs API key
    r = requests.get(
        "https://api.twitter.com/1.1/users/show.json?screen_name=ActivityWatchIt"
    )
    d = r.json()
    pprint(d)
    followers = d["followers_count"]
    print("Followers: ", followers)


def load_releases_csv(path: str) -> dict[str, date]:
    """Load already-known release dates from a `date,tag` CSV, keyed by tag."""
    known: dict[str, date] = {}
    if not os.path.exists(path):
        return known
    with open(path) as f:
        next(f, None)  # skip header
        for line in f:
            line = line.strip()
            if not line:
                continue
            d, tag = line.split(",")
            known[tag] = date.fromisoformat(d)
    return known


def releases(known: dict[str, date] | None = None):
    """
    Fetch all releases and their dates from the GitHub API.
    NOTE: Needs to use the commit date, not the release date, for some releases that had to be re-released (like v0.10.0).

    Release/tag dates never change, so we only fetch the commit for tags not
    already in `known`. This keeps the steady-state cost at a single API call
    (the releases list) instead of one-per-release, which used to exhaust the
    rate limit and break CI.
    """
    known = known or {}
    d = github_get_all(RELEASES_PATH)

    releases: dict[str, date] = {}
    for release in d:
        tag = release["tag_name"]
        if tag in known:
            releases[tag] = known[tag]
            continue
        # We need to fetch the commit of the tag and use its date
        commit = github_get(
            "/repos/ActivityWatch/activitywatch/commits/" + tag
        )
        releases[tag] = (
            datetime.strptime(
                commit["commit"]["committer"]["date"],
                "%Y-%m-%dT%H:%M:%SZ",
            )
            .replace(tzinfo=timezone.utc)
            .date()
        )
    return releases


if __name__ == "__main__":
    if "--releases" in sys.argv:
        # Reuse dates already collected so we only hit the API for new releases.
        known = load_releases_csv("data/releases.csv")
        rels = releases(known)
        if "--csv" in sys.argv:
            print("date,tag")
            for tag, d in sorted(rels.items(), key=lambda x: x[1]):
                print(f"{d},{tag}")
        else:
            pprint(rels)
    elif "--assets" in sys.argv:
        # Log per-asset download counts as an append-only time series. Only
        # emit assets whose count changed since last logged, so old releases
        # (which no longer gain downloads) don't bloat the CSV every run.
        last = load_asset_counts(ASSETS_CSV)
        ts = datetime.now(tz=timezone.utc).isoformat()
        changed = [
            r
            for r in downloads_by_asset()
            if last.get((r["tag"], r["asset"])) != r["downloads"]
        ]
        if "--csv" in sys.argv:
            # Emit data rows only; the committed CSV carries the header and CI
            # appends to it (like data/stats.csv).
            w = csv.writer(sys.stdout, lineterminator="\n")
            for r in sorted(changed, key=lambda r: (r["tag"], r["asset"])):
                w.writerow([ts, r["tag"], r["asset"], r["platform"], r["downloads"]])
        else:
            pprint(changed)
    else:
        s = stars()
        d = downloads()

        if "--csv" in sys.argv:
            print(f"{datetime.now(tz=timezone.utc).isoformat()},{d},{s}")
        else:
            print("Downloads: ", d)
            print("Stars:     ", s)
