"""Offline API/CLI/report contracts; response identifiers and messages are synthetic."""
from __future__ import annotations

import copy
import json

import pytest
import requests
from click.testing import CliRunner

import crash_reports
import vitals

QUERY = {"days": 7, "type": "all", "limit": 100,
         "start_time": "2026-09-01T04:00:00Z", "end_time": "2026-09-08T04:00:00Z"}
ISSUE = {"name": "apps/net.activitywatch.android/123", "type": "CRASH",
         "cause": "java.lang.NullPointerException", "location": "ChromeWatcher.onAccessibilityEvent",
         "errorReportCount": "791", "distinctUsers": "99",
         "issueUri": "https://play.google.com/console/issue/123",
         "lastErrorReportTime": "2026-09-08T01:00:00Z"}
REPORT = {"name": "apps/net.activitywatch.android/sample123", "issue": ISSUE["name"],
          "type": "CRASH", "eventTime": "2026-09-08T01:00:00Z",
          "appVersion": {"versionCode": "27", "versionName": "0.12.1"},
          "deviceModel": {"deviceId": {"buildDevice": "PRIVATE_DEVICE"}},
          "vcsInformation": "PRIVATE_BUILD_PATH",
          "reportText": "java.lang.NullPointerException: PRIVATE_MESSAGE\n"
                        "  at net.activitywatch.android.ChromeWatcher.onAccessibilityEvent(ChromeWatcher.kt:76)"}


def snapshot(issues=None, query=None):
    return crash_reports.build_snapshot(
        "net.activitywatch.android", query or QUERY, issues if issues is not None else [ISSUE],
        {ISSUE["name"]: REPORT}, "2026-09-08T04:20:00Z", False)


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status != 200:
            raise requests.HTTPError(f"HTTP {self.status}")


def test_snapshot_keeps_play_identity_and_only_allowlisted_sample_fields(monkeypatch):
    def forbidden(*args):
        pytest.fail("Play issue identity must never be replaced by a local hash")
    monkeypatch.setattr(crash_reports, "fallback_fingerprint", forbidden)
    result = snapshot()
    row = result["clusters"][0]
    assert row["identity"] == ISSUE["name"]
    assert row["fingerprint"] is None
    assert row["report_count"] == 791
    assert row["sample"] == {"report_id": REPORT["name"], "event_time": REPORT["eventTime"],
                             "version_code": "27", "version_name": "0.12.1"}
    payload = crash_reports.json_text(result)
    assert "PRIVATE" not in payload
    assert "reportText" not in payload
    assert "distinctUsers" not in payload
    assert row["severity"] == "unclassified"


def test_snapshot_order_and_fallback_are_deterministic():
    second = dict(ISSUE, name="apps/net.activitywatch.android/222", errorReportCount="999")
    assert crash_reports.json_text(snapshot([ISSUE, second])) == crash_reports.json_text(
        snapshot([second, ISSUE]))
    imported = {k: v for k, v in ISSUE.items() if k != "name"}
    row = snapshot([imported])["clusters"][0]
    assert row["identity_source"] == "heuristic_fingerprint"
    assert row["identity"] == row["fingerprint"]
    assert row["identity"] == snapshot([imported])["clusters"][0]["identity"]


def test_unknown_count_is_not_zero():
    missing = {k: v for k, v in ISSUE.items() if k != "errorReportCount"}
    result = snapshot([missing])
    assert result["clusters"][0]["report_count"] is None
    assert "unknown" in crash_reports.render_markdown(result)


def test_comparison_tracks_movement_without_declaring_missing_clusters_fixed():
    old = snapshot([ISSUE, dict(ISSUE, name="apps/net.activitywatch.android/gone")])
    new = snapshot([dict(ISSUE, errorReportCount="7"),
                    dict(ISSUE, name="apps/net.activitywatch.android/new")])
    comparison = crash_reports.compare_snapshots(new, old)
    assert comparison["report_count_deltas"][ISSUE["name"]] == -784
    assert comparison["not_observed"] == ["apps/net.activitywatch.android/gone"]
    assert comparison["newly_observed"] == ["apps/net.activitywatch.android/new"]
    new["comparison"] = comparison
    rendered = crash_reports.render_markdown(new)
    assert "-784" in rendered
    assert "not proven fixed" in rendered
    assert "not all affected versions" in rendered


@pytest.mark.parametrize("field,value", [("days", 1), ("type", "anr"), ("limit", 10)])
def test_different_query_is_not_a_count_trend(field, value):
    result = crash_reports.compare_snapshots(snapshot(query={**QUERY, field: value}), snapshot())
    assert result["status"] == "incompatible_query"
    assert "report_count_deltas" not in result


def test_persistence_retains_changed_same_time_snapshots_and_dispositions(tmp_path):
    old = snapshot()
    first = crash_reports.persist_snapshot(tmp_path, old)
    original = first.read_bytes()
    assert crash_reports.persist_snapshot(tmp_path, old) == first
    assert len(list((tmp_path / "history").glob("*.json"))) == 1
    decision = {ISSUE["name"]: {"status": "investigating", "issue_url": "https://github.com/ActivityWatch/aw-android/issues/185"}}
    disposition_file = tmp_path / "dispositions.json"
    disposition_file.write_text(json.dumps(decision))
    newer = copy.deepcopy(old)
    newer["clusters"][0]["report_count"] = 5
    newer["comparison"] = crash_reports.compare_snapshots(newer, old)
    second = crash_reports.persist_snapshot(tmp_path, newer)
    assert first != second
    assert first.read_bytes() == original
    assert json.loads((tmp_path / "current.json").read_text()) == newer
    assert json.loads(disposition_file.read_text()) == decision
    assert "-786" in (tmp_path / "current.md").read_text()


def test_current_reports_roll_back_together_if_markdown_replace_fails(tmp_path, monkeypatch):
    old = snapshot()
    crash_reports.persist_snapshot(tmp_path, old)
    previous_json = (tmp_path / "current.json").read_text()
    previous_md = (tmp_path / "current.md").read_text()
    newer = copy.deepcopy(old)
    newer["clusters"][0]["report_count"] = 5
    original = crash_reports._replace

    def fail_markdown(source, destination):
        if destination.name == "current.md":
            raise OSError("simulated publish failure")
        original(source, destination)

    monkeypatch.setattr(crash_reports, "_replace", fail_markdown)
    with pytest.raises(OSError, match="simulated publish failure"):
        crash_reports.persist_snapshot(tmp_path, newer)
    assert (tmp_path / "current.json").read_text() == previous_json
    assert (tmp_path / "current.md").read_text() == previous_md
    assert not [path for path in tmp_path.iterdir() if path.name.startswith(".")]
    monkeypatch.setattr(crash_reports, "_replace", original)
    newer["comparison"] = crash_reports.compare_snapshots(newer, old)
    crash_reports.persist_snapshot(tmp_path, newer)
    assert json.loads((tmp_path / "current.json").read_text()) == newer
    assert "-786" in (tmp_path / "current.md").read_text()


def test_partial_first_publish_does_not_leave_json_without_markdown(tmp_path, monkeypatch):
    original = crash_reports._replace

    def fail_markdown(source, destination):
        if destination.name == "current.md":
            raise OSError("simulated publish failure")
        original(source, destination)

    monkeypatch.setattr(crash_reports, "_replace", fail_markdown)
    with pytest.raises(OSError, match="simulated publish failure"):
        crash_reports.persist_snapshot(tmp_path, snapshot())
    assert not (tmp_path / "current.json").exists()
    assert not (tmp_path / "current.md").exists()
    assert not [path for path in tmp_path.iterdir() if path.name.startswith(".")]


def test_fetch_paginates_and_records_top_n_truncation(monkeypatch):
    monkeypatch.setattr(vitals, "_access_token", lambda _: "test-token")
    calls = []
    responses = iter([
        {"errorIssues": [ISSUE], "nextPageToken": "page2"},
        {"errorIssues": [dict(ISSUE, name="second"), dict(ISSUE, name="third")]},
    ])
    def get(url, **kwargs):
        calls.append(copy.deepcopy(kwargs["params"]))
        return Response(next(responses))
    monkeypatch.setattr(vitals.requests, "get", get)
    rows, _, truncated = vitals.fetch_error_clusters("pkg", "all", 2, None, QUERY)
    assert len(rows) == 2 and truncated
    assert calls[0]["orderBy"] == "errorReportCount desc"
    assert calls[1]["pageToken"] == "page2"
    assert calls[0]["interval.startTime.day"] == 1
    assert calls[0]["interval.endTime.hours"] == 4
    assert calls[0]["interval.endTime.timeZone.id"] == "UTC"
    assert "filter" not in calls[0]


def test_corrupt_existing_archive_is_reported_not_silently_reused(tmp_path):
    result = snapshot()
    archive = crash_reports.persist_snapshot(tmp_path, result)
    archive.write_text('{"schema_')  # An interrupted writer from an older version.
    with pytest.raises(ValueError, match="Existing snapshot differs"):
        crash_reports.persist_snapshot(tmp_path, result)
    assert archive.read_text() == '{"schema_'  # Never overwrite historical evidence.
    assert not list((tmp_path / "history").glob("*.tmp"))


def test_sample_uses_same_query_window(monkeypatch):
    def get(url, **kwargs):
        assert kwargs["params"]["filter"] == "errorIssueId = 123"
        assert kwargs["params"]["interval.startTime.day"] == 1
        return Response({"errorReports": [REPORT]})
    monkeypatch.setattr(vitals.requests, "get", get)
    assert vitals.fetch_sample_report("pkg", "123", "token", QUERY) == REPORT


def test_cli_json_empty_is_parseable_and_human_default_is_preserved(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr(vitals, "fetch_error_clusters", lambda *args: ([], "token", False))
    result = runner.invoke(vitals.cli, ["errors", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["clusters"] == []
    assert runner.invoke(vitals.cli, ["errors"]).stdout == "No error issues found.\n"
    monkeypatch.setattr(vitals, "fetch_error_clusters", lambda *args: ([ISSUE], "token", False))
    result = runner.invoke(vitals.cli, ["errors"])
    assert result.stdout == ("1. [CRASH] 791 reports  ChromeWatcher.onAccessibilityEvent\n"
                             "   java.lang.NullPointerException\n\n")


def test_cli_missing_play_id_uses_fallback_even_when_samples_requested(monkeypatch):
    issue = {k: v for k, v in ISSUE.items() if k != "name"}
    monkeypatch.setattr(vitals, "fetch_error_clusters", lambda *args: ([issue], "token", False))
    def forbidden(*args):
        pytest.fail("Cannot request a sample without a Play issue ID")
    monkeypatch.setattr(vitals, "fetch_sample_report", forbidden)
    result = CliRunner().invoke(vitals.cli, ["errors", "--json", "--stacktraces"])
    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)["clusters"][0]
    assert row["identity_source"] == "heuristic_fingerprint"
    assert row["sample"] is None


def test_cli_consumer_preserves_history_and_decisions_on_refresh_and_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(vitals, "fetch_error_clusters", lambda *args: ([ISSUE], "token", False))
    monkeypatch.setattr(vitals, "fetch_sample_report", lambda *args: REPORT)
    (tmp_path / "dispositions.json").write_text(json.dumps({ISSUE["name"]: {"status": "linked"}}))
    runner = CliRunner()
    command = ["errors", "--json", "--stacktraces", "--update-dir", str(tmp_path)]
    first = runner.invoke(vitals.cli, command)
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["clusters"][0]["disposition"] == {"status": "linked"}
    second = runner.invoke(vitals.cli, command)
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["comparison"]["report_count_deltas"][ISSUE["name"]] == 0
    baseline = (tmp_path / "current.json").read_bytes()
    archives = list((tmp_path / "history").glob("*.json"))
    def fail(*args):
        raise requests.HTTPError("HTTP 403")
    monkeypatch.setattr(vitals, "fetch_sample_report", fail)
    failed = runner.invoke(vitals.cli, command)
    assert failed.exit_code != 0 and "403" in failed.output
    assert (tmp_path / "current.json").read_bytes() == baseline
    assert list((tmp_path / "history").glob("*.json")) == archives


def test_cli_rejects_unsupported_baseline_without_overwriting(tmp_path, monkeypatch):
    (tmp_path / "current.json").write_text('{"schema_version": 999}')
    monkeypatch.setattr(vitals, "fetch_error_clusters", lambda *args: ([], "token", False))
    result = CliRunner().invoke(vitals.cli, ["errors", "--update-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "Unsupported previous snapshot" in result.output
    assert not (tmp_path / "history").exists()
