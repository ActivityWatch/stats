"""
High-resolution download sampler for launch windows.

Run it anywhere (laptop, server, VM) during a launch, then submit the files it
writes in a PR. It polls the GitHub API every `--interval` seconds (default 60)
and appends rows only when something changed:

  data/hf/stats.csv         timestamp,downloads,stars     (same schema as data/stats.csv)
  data/hf/stats-assets.csv  timestamp,tag,asset,platform,downloads

Downloads exclude Tauri updater assets, like collect_stats.downloads(); the
per-asset file keeps them (latest.json fetches = update checks).

Uses conditional requests (ETag): an unchanged response is a 304, which does
not count against the rate limit. Set GITHUB_TOKEN (e.g. `GITHUB_TOKEN=$(gh auth token)`)
for the 5000/h limit; unauthenticated (60/h) needs --interval >= 120.

    GITHUB_TOKEN=$(gh auth token) uv run python3 sample_hf.py --until 2026-10-20

Stop with Ctrl-C; a restart resumes from the files. Coarsen before submitting
with `--trim MINUTES` (keeps the last row per series per MINUTES-wide bucket).
"""

import argparse
import csv
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import requests

from collect_stats import RELEASES_PATH, is_updater_asset, platform

HF_DIR = "data/hf"
STATS_CSV = os.path.join(HF_DIR, "stats.csv")
ASSETS_CSV = os.path.join(HF_DIR, "stats-assets.csv")
STATS_FIELDS = ["timestamp", "downloads", "stars"]
ASSET_FIELDS = ["timestamp", "tag", "asset", "platform", "downloads"]
LOCK_PATH = os.path.join(HF_DIR, ".lock")


def _try_lock(f) -> bool:
    """Non-blocking exclusive lock on an open file (fcntl on Unix, msvcrt on Windows)."""
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


@contextmanager
def exclusive_lock():
    """Held by a running sampler for its lifetime, and by --trim, so trimming
    can never race with appends."""
    os.makedirs(HF_DIR, exist_ok=True)
    with open(LOCK_PATH, "a+") as f:
        if not _try_lock(f):
            raise SystemExit(f"another sample_hf.py is running ({LOCK_PATH} is locked)")
        yield


class ConditionalClient:
    """GitHub GET with per-URL ETag caching (304s are free) and Link pagination."""

    def __init__(self, token: str | None):
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/vnd.github+json"
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.cache: dict[str, tuple[str, object, str | None]] = {}

    def get(self, url: str) -> tuple[object, str | None]:
        """Return (json, next_page_url)."""
        headers = {}
        if url in self.cache:
            headers["If-None-Match"] = self.cache[url][0]
        r = self.session.get(url, headers=headers, timeout=30)
        if r.status_code == 304:
            _, data, nxt = self.cache[url]
            return data, nxt
        r.raise_for_status()
        nxt = r.links.get("next", {}).get("url")
        self.cache[url] = (r.headers.get("ETag", ""), r.json(), nxt)
        return self.cache[url][1], nxt

    def assets(self) -> list[dict]:
        rows = []
        url: str | None = f"https://api.github.com{RELEASES_PATH}"
        while url:
            releases, url = self.get(url)
            assert isinstance(releases, list)
            for release in releases:
                for a in release["assets"]:
                    rows.append(
                        {
                            "tag": release["tag_name"],
                            "asset": a["name"],
                            "platform": platform(a["name"]),
                            "downloads": a["download_count"],
                        }
                    )
        return rows

    def stars(self) -> int:
        repo, _ = self.get("https://api.github.com/repos/ActivityWatch/activitywatch")
        assert isinstance(repo, dict)
        return int(repo["stargazers_count"])


def _last_rows(path: str, key_fields: list[str]) -> dict[tuple, dict]:
    last: dict[tuple, dict] = {}
    if os.path.exists(path):
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                last[tuple(row[k] for k in key_fields)] = row
    return last


def _append(path: str, fields: list[str], rows: list[dict]) -> None:
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def sample_once(client: ConditionalClient, state: dict) -> tuple[int, int]:
    """Take one sample; append changed rows. Returns (#asset rows, #stats rows)."""
    ts = datetime.now(tz=timezone.utc).isoformat()
    assets = client.assets()
    changed = [
        {"timestamp": ts, **a}
        for a in assets
        if state["assets"].get((a["tag"], a["asset"])) != a["downloads"]
    ]
    for a in changed:
        state["assets"][(a["tag"], a["asset"])] = a["downloads"]
    if changed:
        _append(ASSETS_CSV, ASSET_FIELDS, changed)

    total = sum(a["downloads"] for a in assets if not is_updater_asset(a["asset"]))
    stars = client.stars()
    stats_rows = []
    if (total, stars) != state["stats"]:
        state["stats"] = (total, stars)
        stats_rows = [{"timestamp": ts, "downloads": total, "stars": stars}]
        _append(STATS_CSV, STATS_FIELDS, stats_rows)
    return len(changed), len(stats_rows)


def load_state() -> dict:
    assets = {
        k: int(r["downloads"])
        for k, r in _last_rows(ASSETS_CSV, ["tag", "asset"]).items()
    }
    stats: tuple[int, int] | None = None
    if os.path.exists(STATS_CSV):
        with open(STATS_CSV, newline="") as f:
            rows = list(csv.DictReader(f))
        if rows:
            stats = (int(rows[-1]["downloads"]), int(rows[-1]["stars"]))
    return {"assets": assets, "stats": stats}


def trim(minutes: int) -> None:
    """Keep only the last row per series in each `minutes`-wide bucket."""
    bucket_s = minutes * 60
    for path, key in [(STATS_CSV, []), (ASSETS_CSV, ["tag", "asset"])]:
        if not os.path.exists(path):
            continue
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
        kept: dict[tuple, dict] = {}
        for r in rows:
            b = int(datetime.fromisoformat(r["timestamp"]).timestamp()) // bucket_s
            kept[(b, *(r[k] for k in key))] = r  # later rows overwrite earlier ones
        out = sorted(kept.values(), key=lambda r: r["timestamp"])
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(out)
        print(f"{path}: {len(rows)} -> {len(out)} rows")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--interval", type=int, default=60, help="seconds between samples (default 60)"
    )
    p.add_argument("--until", help="stop at this UTC date/time (ISO), e.g. 2026-10-20")
    p.add_argument("--once", action="store_true", help="take one sample and exit")
    p.add_argument(
        "--trim", type=int, metavar="MINUTES", help="coarsen the files and exit"
    )
    args = p.parse_args()

    if args.interval <= 0:
        p.error("--interval must be positive")
    if args.trim is not None:
        if args.trim <= 0:
            p.error("--trim must be a positive number of minutes")
        with exclusive_lock():
            trim(args.trim)
        return
    token = os.getenv("GITHUB_TOKEN")
    if not token and args.interval < 120:
        p.error(
            "set GITHUB_TOKEN for intervals under 120 s (unauthenticated limit is 60/h)"
        )
    until = None
    if args.until:
        until = datetime.fromisoformat(args.until)
        # Naive times are UTC; times with an offset are converted.
        until = (
            until.replace(tzinfo=timezone.utc)
            if until.tzinfo is None
            else until.astimezone(timezone.utc)
        )
    with exclusive_lock():
        run(ConditionalClient(token), args.interval, until, args.once)


def run(client, interval: int, until, once: bool) -> None:
    state = load_state()
    while True:
        started = time.monotonic()
        try:
            n_assets, n_stats = sample_once(client, state)
            print(
                f"{datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M:%S}Z +{n_assets} asset rows, +{n_stats} stats rows",
                flush=True,
            )
        except requests.RequestException as e:
            print(f"sample failed, retrying next interval: {e}", flush=True)
        if once or (until and datetime.now(tz=timezone.utc) >= until):
            return
        time.sleep(max(0.0, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
