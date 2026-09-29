import android_ratings as ar
from android_ratings import _is_valid_rating, fetch_rating_series


def test_zero_and_garbage_ratings_rejected():
    assert not _is_valid_rating("0.0")
    assert not _is_valid_rating("0")
    assert not _is_valid_rating("abc")
    assert _is_valid_rating("3.2884")
    assert _is_valid_rating("5.00")


def test_fetch_rating_series_skips_placeholder_zero(monkeypatch):
    """Collection path, not just the validator: 0.0 must not enter the series."""
    report = (
        "Date,Package Name,Total Average Rating\n"
        "2026-09-23,net.activitywatch.android,3.3999\n"
        "2026-09-24,net.activitywatch.android,0.0\n"
        "2026-09-25,net.activitywatch.android,3.2884\n"
        "2026-09-26,net.activitywatch.android,abc\n"
        "2026-09-27,net.activitywatch.android,5.00\n"
    ).encode("utf-16")

    monkeypatch.setattr(ar, "_token", lambda creds: "tok")
    monkeypatch.setattr(
        ar, "list_rating_files", lambda bucket, package, token: ["overview.csv"]
    )
    monkeypatch.setattr(ar, "_download", lambda bucket, name, token: report)

    series = fetch_rating_series("bucket", "net.activitywatch.android", credentials=None)
    assert series == {
        "2026-09-23": "3.3999",
        "2026-09-25": "3.2884",
        "2026-09-27": "5.00",
    }
