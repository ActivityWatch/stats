import re
from pprint import pprint
import sys
import os
from datetime import datetime, timezone, date

import requests


def github_get(path: str):
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get(f"https://api.github.com{path}", headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def downloads(verbose=False):
    d = github_get("/repos/ActivityWatch/activitywatch/releases?per_page=100")

    downloads = 0
    for release in d:
        if verbose:
            print("Release: ", release["tag_name"])
        for asset in release["assets"]:
            platform_match = re.findall("(macos|darwin|linux|windows)", asset["name"])
            count = asset["download_count"]
            if verbose:
                platform = platform_match[0] if platform_match else "unknown"
                print(" - {}: {}".format(platform, count))

            downloads += count
    return downloads


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


def releases():
    """
    Fetch all releases and their dates from the GitHub API.
    NOTE: Needs to use the commit date, not the release date, for some releases that had to be re-released (like v0.10.0).
    """
    d = github_get("/repos/ActivityWatch/activitywatch/releases?per_page=100")

    releases: dict[str, date] = {}
    for release in d:
        # We need to fetch the commit of the tag and use its date
        commit = github_get(
            "/repos/ActivityWatch/activitywatch/commits/" + release["tag_name"]
        )
        releases[release["tag_name"]] = (
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
        if "--csv" in sys.argv:
            print("date,tag")
            for tag, d in sorted(releases().items(), key=lambda x: x[1]):
                print(f"{d},{tag}")
        else:
            pprint(releases())
    else:
        s = stars()
        d = downloads()

        if "--csv" in sys.argv:
            print(f"{datetime.now(tz=timezone.utc).isoformat()},{d},{s}")
        else:
            print("Downloads: ", d)
            print("Stars:     ", s)
