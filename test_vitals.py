"""Offline tests for the vitals query/parse helpers.

These cover the pieces that can be wrong without the API ever telling us:
the dimensioned query body, per-version row parsing, and the stalled-feed
warning that exists because a stalled collector otherwise exits 0 silently.
"""
from datetime import date, timedelta

import vitals


END_DT = {"year": 2026, "month": 8, "day": 26}


def _row(day: int, version: str | None, value: float | None,
         metric: str = "userPerceivedCrashRate") -> dict:
    row: dict = {"startTime": {"year": 2026, "month": 8, "day": day}, "metrics": []}
    if version is not None:
        row["dimensions"] = [{"dimension": "versionCode", "int64Value": version}]
    if value is not None:
        row["metrics"] = [{"metric": metric, "decimalValue": {"value": str(value)}}]
    return row


def test_build_body_omits_dimensions_by_default():
    body = vitals._build_body(END_DT, 7, "userPerceivedCrashRate")
    assert "dimensions" not in body


def test_build_body_includes_requested_dimensions():
    body = vitals._build_body(END_DT, 7, "userPerceivedCrashRate", ("versionCode",))
    assert body["dimensions"] == ["versionCode"]
    assert body["timelineSpec"]["startTime"]["day"] == 19


def test_dimension_raw_reads_int64():
    assert vitals._dimension_raw(_row(20, "1000", 0.05), "versionCode") == "1000"


def test_dimension_raw_ignores_label():
    row = {"dimensions": [{"dimension": "versionCode", "int64Value": "1000",
                           "valueLabel": "0.14.0b2"}]}
    assert vitals._dimension_raw(row, "versionCode") == "1000"


def test_dimension_raw_missing_is_none():
    assert vitals._dimension_raw(_row(20, None, 0.05), "versionCode") is None


def test_dimension_value_reads_int64():
    assert vitals._dimension_value(_row(20, "1000", 0.05), "versionCode") == "1000"


def test_dimension_value_prefers_label_when_it_adds_information():
    row = {"dimensions": [{"dimension": "versionCode", "int64Value": "1000",
                           "valueLabel": "0.14.0b2"}]}
    assert vitals._dimension_value(row, "versionCode") == "1000 (0.14.0b2)"


def test_dimension_value_missing_dimension_is_none():
    assert vitals._dimension_value(_row(20, None, 0.05), "versionCode") is None
    assert vitals._dimension_value(_row(20, "1000", 0.05), "apiLevel") is None


def test_parse_rows_reads_the_requested_metric_only():
    rows = [_row(20, "1000", 0.05), _row(21, "1000", 0.03)]
    assert vitals._parse_rows(rows, "userPerceivedCrashRate") == [
        ("2026-08-20", 0.05),
        ("2026-08-21", 0.03),
    ]
    # A different metric name yields no values rather than the wrong number.
    assert [v for _, v in vitals._parse_rows(rows, "userPerceivedAnrRate")] == [None, None]


def test_fresh_feed_produces_no_warning():
    today = date.today()
    assert vitals._freshness_warning(
        {"year": today.year, "month": today.month, "day": today.day}
    ) is None


def test_stalled_feed_warns_with_the_lag():
    stale = date.today() - timedelta(days=vitals.STALE_FRESHNESS_DAYS + 4)
    warning = vitals._freshness_warning(
        {"year": stale.year, "month": stale.month, "day": stale.day}
    )
    assert warning is not None
    assert str(stale) in warning
    assert "stalled" in warning


def test_freshness_warning_tolerates_a_malformed_date():
    assert vitals._freshness_warning({}) is None
    assert vitals._freshness_warning({"year": 2026, "month": 13, "day": 40}) is None


def _labeled_row(day: int, code: str, label: str | None, value: float | None,
                 metric: str = "userPerceivedCrashRate") -> dict:
    """Like _row but with an explicit valueLabel on the versionCode dimension."""
    row: dict = {"startTime": {"year": 2026, "month": 8, "day": day}, "metrics": []}
    dim: dict = {"dimension": "versionCode", "int64Value": code}
    if label is not None:
        dim["valueLabel"] = label
    row["dimensions"] = [dim]
    if value is not None:
        row["metrics"] = [{"metric": metric, "decimalValue": {"value": str(value)}}]
    return row


def test_by_version_same_code_different_label_merges():
    """P1: rows with the same versionCode but varying valueLabel must land in one bucket."""
    rows = [
        _labeled_row(20, "1000", "0.14.0b2", 0.05),
        _labeled_row(21, "1000", None, 0.03),   # label absent on day 21
        _labeled_row(22, "1000", "0.14.0b2", 0.04),
    ]
    metric = "userPerceivedCrashRate"
    per_version: dict = {}
    version_labels: dict = {}
    for row in rows:
        code = vitals._dimension_raw(row, "versionCode")
        assert code is not None
        display = vitals._dimension_value(row, "versionCode")
        if display and display != code:
            version_labels[code] = display
        for _, val in vitals._parse_rows([row], metric):
            if val is not None:
                per_version.setdefault(code, []).append(val)
    # Must be exactly one bucket keyed by the raw code, not by display strings
    assert list(per_version.keys()) == ["1000"]
    assert len(per_version["1000"]) == 3
    # Label should have been captured as the full display string
    assert version_labels.get("1000") == "1000 (0.14.0b2)"


def test_by_version_csv_escapes_comma_in_label(monkeypatch, tmp_path):
    """P2: a comma in a version label must not corrupt the CSV output."""
    import io
    from click.testing import CliRunner

    # Patch fetch_rows to return a row whose label contains a comma
    fake_row = _labeled_row(20, "1000", "label,with,commas", 0.05)
    monkeypatch.setattr(
        vitals, "fetch_rows",
        lambda *a, **kw: ([fake_row], END_DT)
    )

    runner = CliRunner()
    result = runner.invoke(
        vitals.cli,
        ["by-version", "--as-csv", "--package", "net.activitywatch.android"],
    )
    assert result.exit_code == 0, result.output
    import csv, io as _io
    rows = list(csv.reader(_io.StringIO(result.output)))
    # Header + one data row; the label's commas must not create extra columns
    assert rows[0] == ["version", "days", "mean"]
    assert len(rows[1]) == 3
