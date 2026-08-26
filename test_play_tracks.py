"""Offline tests for play_tracks.py — no network, no credentials."""
from __future__ import annotations

import csv

import pytest
import requests

import play_tracks


# --- fixtures --------------------------------------------------------------

# Shape of a real Publisher API tracks response mid-rollout: production is
# serving a completed release *and* staging its replacement at 10%.
TRACKS_MID_ROLLOUT = [
    {
        "track": "production",
        "releases": [
            {"name": "0.14.0b2", "versionCodes": ["31"], "status": "inProgress",
             "userFraction": 0.1},
            {"name": "0.12.1", "versionCodes": ["27"], "status": "completed"},
        ],
    },
    {
        "track": "internal",
        "releases": [
            {"name": "0.14.0b4", "versionCodes": ["33"], "status": "completed"},
        ],
    },
]


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


# --- parse_tracks ----------------------------------------------------------

def test_parse_flattens_one_row_per_release():
    rows = play_tracks.parse_tracks(TRACKS_MID_ROLLOUT, "2026-08-26")
    assert len(rows) == 3
    assert {r["track"] for r in rows} == {"production", "internal"}
    assert all(r["date"] == "2026-08-26" for r in rows)


def test_parse_keeps_both_releases_on_a_staged_track():
    """The rollout question is unanswerable if the staged release is dropped."""
    rows = play_tracks.parse_tracks(TRACKS_MID_ROLLOUT, "2026-08-26")
    prod = [r for r in rows if r["track"] == "production"]
    assert len(prod) == 2
    staged = next(r for r in prod if r["status"] == "inProgress")
    assert staged["user_fraction"] == "0.1"
    assert staged["version_codes"] == "31"


def test_completed_release_reports_full_fraction():
    """The API omits userFraction on a completed release; '' would read as unknown."""
    rows = play_tracks.parse_tracks(TRACKS_MID_ROLLOUT, "2026-08-26")
    done = next(r for r in rows if r["release_name"] == "0.12.1")
    assert done["user_fraction"] == "1.0"


def test_parse_joins_multiple_version_codes():
    rows = play_tracks.parse_tracks(
        [{"track": "beta", "releases": [
            {"name": "x", "versionCodes": ["40", "41"], "status": "completed"}]}],
        "2026-08-26",
    )
    assert rows[0]["version_codes"] == "40;41"


def test_parse_tolerates_missing_fields():
    rows = play_tracks.parse_tracks([{"track": "alpha", "releases": [{}]}], "2026-08-26")
    assert rows == [{"date": "2026-08-26", "track": "alpha", "status": "",
                     "user_fraction": "", "version_codes": "", "release_name": ""}]


def test_parse_empty_tracks():
    assert play_tracks.parse_tracks([], "2026-08-26") == []


# --- upsert_csv ------------------------------------------------------------

def test_upsert_writes_header_and_rows(tmp_path):
    path = str(tmp_path / "tracks.csv")
    rows = play_tracks.parse_tracks(TRACKS_MID_ROLLOUT, "2026-08-26")
    assert play_tracks.upsert_csv(path, rows) == 3
    with open(path, newline="") as f:
        got = list(csv.DictReader(f))
    assert list(got[0]) == play_tracks.CSV_HEADER
    assert len(got) == 3


def test_upsert_is_idempotent_within_a_day(tmp_path):
    """The daily collector re-runs; a second run must not duplicate the snapshot."""
    path = str(tmp_path / "tracks.csv")
    rows = play_tracks.parse_tracks(TRACKS_MID_ROLLOUT, "2026-08-26")
    play_tracks.upsert_csv(path, rows)
    assert play_tracks.upsert_csv(path, rows) == 3


def test_upsert_appends_a_new_day_and_keeps_history(tmp_path):
    path = str(tmp_path / "tracks.csv")
    play_tracks.upsert_csv(path, play_tracks.parse_tracks(TRACKS_MID_ROLLOUT, "2026-08-26"))
    play_tracks.upsert_csv(path, play_tracks.parse_tracks(TRACKS_MID_ROLLOUT, "2026-08-27"))
    with open(path, newline="") as f:
        got = list(csv.DictReader(f))
    assert len(got) == 6
    assert {r["date"] for r in got} == {"2026-08-26", "2026-08-27"}


def test_upsert_overwrites_a_changed_same_day_snapshot(tmp_path):
    """Rollout fraction moves during the day; the latest read wins."""
    path = str(tmp_path / "tracks.csv")
    play_tracks.upsert_csv(path, play_tracks.parse_tracks(TRACKS_MID_ROLLOUT, "2026-08-26"))
    advanced = [{"track": "production", "releases": [
        {"name": "0.14.0b2", "versionCodes": ["31"], "status": "inProgress",
         "userFraction": 0.5}]}]
    play_tracks.upsert_csv(path, play_tracks.parse_tracks(advanced, "2026-08-26"))
    with open(path, newline="") as f:
        got = {(r["track"], r["version_codes"]): r for r in csv.DictReader(f)}
    assert got[("production", "31")]["user_fraction"] == "0.5"
    # Only the one row the second run returned survives for today; prior-day
    # rows from other dates are not affected (tested separately).
    assert len(got) == 1


def test_upsert_removes_stale_same_day_release(tmp_path):
    """A release revoked between two same-day runs must not remain in the CSV."""
    path = str(tmp_path / "tracks.csv")
    # Morning: production carries two releases (staged rollout in progress)
    play_tracks.upsert_csv(path, play_tracks.parse_tracks(TRACKS_MID_ROLLOUT, "2026-08-26"))
    # Evening: the in-progress release was paused and removed from the track
    evening = [{"track": "production", "releases": [
        {"name": "0.12.1", "versionCodes": ["27"], "status": "completed"}]},
               {"track": "internal", "releases": [
        {"name": "0.14.0b4", "versionCodes": ["33"], "status": "completed"}]}]
    play_tracks.upsert_csv(path, play_tracks.parse_tracks(evening, "2026-08-26"))
    with open(path, newline="") as f:
        got = list(csv.DictReader(f))
    version_codes = {r["version_codes"] for r in got}
    assert "31" not in version_codes, "revoked release must not survive a rerun"
    assert len(got) == 2


# --- rollout_summary -------------------------------------------------------

def test_summary_names_the_staged_percentage():
    rows = play_tracks.parse_tracks(TRACKS_MID_ROLLOUT, "2026-08-26")
    text = "\n".join(play_tracks.rollout_summary(rows))
    assert "IN PROGRESS — staged at 10%" in text
    assert "complete (100% of users)" in text


def test_summary_flags_a_halted_rollout():
    rows = play_tracks.parse_tracks(
        [{"track": "production", "releases": [
            {"name": "x", "versionCodes": ["9"], "status": "halted"}]}],
        "2026-08-26",
    )
    assert "HALTED" in play_tracks.rollout_summary(rows)[0]


# --- access failure --------------------------------------------------------

@pytest.mark.parametrize("status", [401, 403])
def test_permission_failure_exits_with_distinct_code(status, capsys):
    """A vitals-only service account must produce advice, not a stack trace."""
    with pytest.raises(SystemExit) as exc:
        play_tracks._check_access(FakeResponse(status, text="caller lacks permission"))
    assert exc.value.code == play_tracks.EXIT_NO_ACCESS
    err = capsys.readouterr().err
    assert "Release to testing tracks" in err
    assert "caller lacks permission" in err


def test_check_access_passes_through_success():
    assert play_tracks._check_access(FakeResponse(200)) is None


# --- fetch_tracks ----------------------------------------------------------

def test_fetch_opens_reads_and_always_deletes_the_edit(monkeypatch):
    calls = []
    monkeypatch.setattr(play_tracks, "_access_token", lambda c: "tok")
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: calls.append("post") or FakeResponse(200, {"id": "e1"}))
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: calls.append("get")
                        or FakeResponse(200, {"tracks": TRACKS_MID_ROLLOUT}))
    monkeypatch.setattr(requests, "delete",
                        lambda *a, **k: calls.append("delete") or FakeResponse(204))

    tracks = play_tracks.fetch_tracks("pkg", None)
    assert tracks == TRACKS_MID_ROLLOUT
    assert calls == ["post", "get", "delete"]


def test_fetch_deletes_the_edit_even_when_the_read_fails(monkeypatch):
    """An abandoned edit lingers on the account; cleanup must not depend on success."""
    calls = []
    monkeypatch.setattr(play_tracks, "_access_token", lambda c: "tok")
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, {"id": "e1"}))
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(500, text="boom"))
    monkeypatch.setattr(requests, "delete",
                        lambda *a, **k: calls.append("delete") or FakeResponse(204))

    with pytest.raises(requests.HTTPError):
        play_tracks.fetch_tracks("pkg", None)
    assert calls == ["delete"]


def test_fetch_returns_data_even_when_delete_raises(monkeypatch):
    """A transport error during cleanup must not discard a successful read."""
    monkeypatch.setattr(play_tracks, "_access_token", lambda c: "tok")
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, {"id": "e1"}))
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: FakeResponse(200, {"tracks": TRACKS_MID_ROLLOUT}))
    monkeypatch.setattr(requests, "delete",
                        lambda *a, **k: (_ for _ in ()).throw(
                            requests.exceptions.ConnectionError("timeout")))

    tracks = play_tracks.fetch_tracks("pkg", None)
    assert tracks == TRACKS_MID_ROLLOUT
