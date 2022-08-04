import re
from pprint import pprint
import sys
from datetime import datetime, timezone, date

import requests


def downloads(verbose=False):
    r = requests.get(
        "https://api.github.com/repos/ActivityWatch/activitywatch/releases"
    )
    d = r.json()

    downloads = 0
    for release in d:
        if verbose:
            print("Release: ", release["tag_name"])
        for asset in release["assets"]:
            platform = re.findall("(macos|darwin|linux|windows)", asset["name"])[0]
            count = asset["download_count"]
            if verbose:
                print(" - {}: {}".format(platform, count))

            downloads += asset["download_count"]
    return downloads


def stars():
    r = requests.get("https://api.github.com/repos/ActivityWatch/activitywatch")
    d = r.json()
    return d["stargazers_count"]


def clones():
    # TODO: Needs push access to the repository
    r = requests.get(
        "https://api.github.com/repos/ActivityWatch/activitywatch/traffic/clones?per=day"
    )
    d = r.json()
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
    r = requests.get(
        "https://api.github.com/repos/ActivityWatch/activitywatch/releases?per_page=100"
    )
    r.raise_for_status()
    d = r.json()

    releases: dict[str, date] = {}
    for release in d:
        # We need to fetch the commit of the tag and use its date
        r = requests.get(
            "https://api.github.com/repos/ActivityWatch/activitywatch/commits/"
            + release["tag_name"]
        )
        r.raise_for_status()
        commit = r.json()
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
