"""Offline tests for the vitals query/parse helpers.

These cover the pieces that can be wrong without the API ever telling us:
the dimensioned query body, per-version row parsing, and the stalled-feed
warning that exists because a stalled collector otherwise exits 0 silently.
"""
import json
import time
from datetime import date, timedelta

import pytest
import requests

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
    import csv
    import io as _io
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
    rows = list(csv.reader(_io.StringIO(result.stdout)))
    # Header + one data row; the label's commas must not create extra columns
    assert rows[0] == ["version", "days", "mean"]
    assert len(rows[1]) == 3


# --- Transient-failure retry -------------------------------------------------
#
# Play answers 503 (or drops the connection) often enough that one bad response
# used to discard a whole day's collection (collect-play.yml). The retry is
# deliberately narrow: 5xx and connection resets get a bounded number of
# attempts; 4xx, auth and malformed JSON still fail on the first response.

SAMPLE_REPORT = {"name": "apps/pkg/sample",
                 "reportText": "java.lang.Boom\n  at Foo.bar(Foo.kt:1)"}
FRESHNESS = {"freshnessInfo": {"freshnesses": [
    {"aggregationPeriod": "DAILY", "latestEndTime": END_DT}]}}


def _response(payload=None, status: int = 200, body: bytes | None = None):
    """A real `requests.Response` with a canned body, so no network is involved."""
    resp = requests.Response()
    resp.status_code = status
    resp._content = body if body is not None else json.dumps(payload).encode()
    return resp


@pytest.fixture
def sleeps(monkeypatch):
    """Record backoff delays instead of sleeping."""
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", recorded.append)
    return recorded


def _patch(monkeypatch, name, outcomes, calls):
    def send(url, **kwargs):
        # The pagination loop mutates its params dict between calls.
        calls.append({**kwargs, **({"params": dict(kwargs["params"])}
                                if "params" in kwargs else {})})
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(vitals.requests, name, send)


def test_fetch_sample_stacktrace_retries_a_transient_503(monkeypatch, sleeps):
    """The exact failure that aborted two scheduled runs: one 503 on a sample fetch."""
    calls: list[dict] = []
    _patch(monkeypatch, "get",
           [_response(status=503), _response({"errorReports": [SAMPLE_REPORT]})], calls)

    assert vitals.fetch_sample_stacktrace("pkg", "4022a4eb", "token") == SAMPLE_REPORT["reportText"]
    assert len(calls) == 2
    assert sleeps == [1]
    assert calls[0]["timeout"] == 30  # existing timeout still applies


def test_retry_exhaustion_after_three_attempts_raises(monkeypatch, sleeps):
    calls: list[dict] = []
    _patch(monkeypatch, "get", [_response(status=503) for _ in range(3)], calls)

    with pytest.raises(requests.HTTPError):
        vitals.fetch_sample_report("pkg", "4022a4eb", "token")
    assert len(calls) == 3  # bounded: 3 attempts total
    assert sleeps == [1, 2]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
def test_client_error_fails_once_without_retry(monkeypatch, sleeps, status):
    calls: list[dict] = []
    _patch(monkeypatch, "get", [_response(status=status)], calls)

    with pytest.raises(requests.HTTPError):
        vitals.fetch_sample_report("pkg", "4022a4eb", "token")
    assert len(calls) == 1
    assert sleeps == []


def test_malformed_json_is_not_retried(monkeypatch, sleeps):
    calls: list[dict] = []
    _patch(monkeypatch, "get", [_response(body=b"<html>nope</html>")], calls)

    with pytest.raises(ValueError):
        vitals.fetch_sample_report("pkg", "4022a4eb", "token")
    assert len(calls) == 1
    assert sleeps == []


def test_connection_reset_is_retried(monkeypatch, sleeps):
    calls: list[dict] = []
    _patch(monkeypatch, "get",
           [requests.ConnectionError("Connection reset by peer"),
            _response({"errorReports": [SAMPLE_REPORT]})], calls)

    assert vitals.fetch_sample_report("pkg", "4022a4eb", "token") == SAMPLE_REPORT
    assert len(calls) == 2
    assert sleeps == [1]


def test_ssl_misconfiguration_is_not_retried(monkeypatch, sleeps):
    """A bad certificate is not transient; retrying only delays the real error."""
    calls: list[dict] = []
    _patch(monkeypatch, "get", [requests.exceptions.SSLError("certificate verify failed")], calls)

    with pytest.raises(requests.exceptions.SSLError):
        vitals.fetch_sample_report("pkg", "4022a4eb", "token")
    assert len(calls) == 1
    assert sleeps == []


def test_metric_get_path_retries_a_transient_503(monkeypatch, sleeps):
    calls: list[dict] = []
    _patch(monkeypatch, "get", [_response(status=503), _response(FRESHNESS)], calls)

    assert vitals._daily_freshness("base", "crashRateMetricSet", "token") == END_DT
    assert len(calls) == 2
    assert sleeps == [1]


def test_metric_post_path_retries_a_transient_503(monkeypatch, sleeps):
    calls: list[dict] = []
    _patch(monkeypatch, "post",
           [_response(status=503), _response({"rows": [_row(20, None, 0.05)]})], calls)
    monkeypatch.setattr(vitals, "_access_token", lambda _: "token")
    monkeypatch.setattr(vitals, "_daily_freshness", lambda *a: END_DT)

    rows, end_dt = vitals.fetch_rows("pkg", "crashRateMetricSet", "userPerceivedCrashRate", 7, None)
    assert len(rows) == 1 and end_dt == END_DT
    assert len(calls) == 2
    assert sleeps == [1]
    assert calls[0]["timeout"] == 60


def test_issue_pagination_retries_the_failed_page_only(monkeypatch, sleeps):
    issue = {"name": "apps/pkg/1", "errorReportCount": "10"}
    calls: list[dict] = []
    _patch(monkeypatch, "get",
           [_response({"errorIssues": [issue], "nextPageToken": "page2"}),
            _response(status=503),
            _response({"errorIssues": [dict(issue, name="apps/pkg/2")]})], calls)
    monkeypatch.setattr(vitals, "_access_token", lambda _: "token")

    issues, _, truncated = vitals.fetch_error_clusters("pkg", "all", 10, None)
    assert [i["name"] for i in issues] == ["apps/pkg/1", "apps/pkg/2"]
    assert truncated is False
    assert len(calls) == 3
    assert sleeps == [1]
    assert [call["params"].get("pageToken") for call in calls] == [None, "page2", "page2"]
