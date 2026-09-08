"""Versioned Play error snapshots and an operator-facing report; no raw traces on disk."""
from __future__ import annotations

import hashlib
import html
import json
import os
import tempfile
from pathlib import Path

from crash_signatures import fallback_fingerprint, normalize_trace


def json_text(value: dict) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build_snapshot(package: str, query: dict, issues: list[dict], samples: dict,
                   collected_at: str, truncated: bool, dispositions: dict | None = None) -> dict:
    clusters = []
    for issue in issues:
        issue_id = issue.get("name") or None
        sample = samples.get(issue_id, {})
        trace = sample.get("reportText", "")
        fingerprint = None if issue_id else fallback_fingerprint(
            issue.get("type", ""), issue.get("cause", ""), issue.get("location", ""), trace)
        identity = issue_id or fingerprint
        raw_count = issue.get("errorReportCount")
        version = sample.get("appVersion", {})
        clusters.append({
            "identity": identity,
            "identity_source": "play_issue" if issue_id else "heuristic_fingerprint",
            "issue_id": issue_id,
            "fingerprint": fingerprint,
            "type": issue.get("type", ""),
            "report_count": int(raw_count) if raw_count is not None else None,
            "severity": "unclassified",
            "cause": issue.get("cause", ""),
            "location": issue.get("location", ""),
            "issue_url": issue.get("issueUri", ""),
            "last_report_time": issue.get("lastErrorReportTime"),
            "trace": normalize_trace(trace),
            "sample": {
                "report_id": sample.get("name"),
                "event_time": sample.get("eventTime"),
                "version_code": version.get("versionCode"),
                "version_name": version.get("versionName"),
            } if sample else None,
            "disposition": (dispositions or {}).get(identity, {}),
        })
    clusters.sort(key=lambda row: (
        -(row["report_count"] if row["report_count"] is not None else -1), row["identity"]))
    return {
        "schema_version": 1,
        "package": package,
        "collected_at": collected_at,
        "query": query,
        "coverage": {"returned": len(clusters), "truncated": truncated},
        "count_source": "Play errorReportCount within query interval and filter",
        "clusters": clusters,
    }


def compare_snapshots(current: dict, previous: dict | None) -> dict:
    if previous is None:
        return {"status": "no_baseline"}
    if previous.get("schema_version") != 1:
        raise ValueError("Unsupported previous snapshot schema_version")
    # Compare equal-sized rolling windows, never pretend differing selections are a trend.
    keys = ("days", "type", "limit")
    if (current["package"] != previous["package"] or
            any(current["query"][k] != previous["query"][k] for k in keys)):
        return {"status": "incompatible_query", "previous_collected_at": previous["collected_at"]}
    old = {row["identity"]: row for row in previous["clusters"]}
    new = {row["identity"]: row for row in current["clusters"]}
    deltas = {}
    for identity in sorted(old.keys() & new.keys()):
        before, after = old[identity]["report_count"], new[identity]["report_count"]
        deltas[identity] = after - before if before is not None and after is not None else None
    return {
        "status": "comparable",
        "previous_collected_at": previous["collected_at"],
        "newly_observed": sorted(new.keys() - old.keys()),
        "not_observed": sorted(old.keys() - new.keys()),
        "report_count_deltas": deltas,
    }


def _cell(value) -> str:
    return html.escape(str(value or ""), quote=False).replace("|", "\\|").replace("\n", " ")


def render_markdown(snapshot: dict) -> str:
    query, comparison = snapshot["query"], snapshot.get("comparison", {})
    deltas = comparison.get("report_count_deltas", {})
    lines = [
        f"# Android error clusters: {_cell(snapshot['package'])}", "",
        f"Collected: {snapshot['collected_at']}. Window: {query['start_time']} → {query['end_time']} (UTC).",
        f"Type: {query['type']}; ranked by reports; limit: {query['limit']}; "
        f"truncated: {str(snapshot['coverage']['truncated']).lower()}.", "",
        "Counts are reports in the query window, not users or lifetime totals. "
        "Severity is unclassified. Play-reported causes are root-cause hypotheses, not confirmed diagnoses.",
        "Missing clusters are not observed in this selection; they are not proven fixed. "
        "Play may regroup issues and change their IDs.", "",
        f"Comparison: {comparison.get('status', 'no_baseline')}. "
        f"Previous collection: {comparison.get('previous_collected_at', 'none')}.", "",
        "| Reports | Δ reports | Type | Play-reported cause / location | Sample version | Identity | Disposition |",
        "| ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in snapshot["clusters"]:
        delta = deltas.get(row["identity"])
        sample, disposition = row["sample"] or {}, row["disposition"]
        count = row["report_count"] if row["report_count"] is not None else "unknown"
        version = " / ".join(str(sample[k]) for k in ("version_code", "version_name") if sample.get(k))
        disposition_text = " — ".join(str(disposition[k]) for k in ("status", "issue_url", "note")
                                      if disposition.get(k))
        lines.append("| " + " | ".join(_cell(value) for value in (
            str(count), f"{delta:+d}" if delta is not None else "—", row["type"],
            f"{row['cause']} / {row['location']}", version or "unavailable",
            row["identity"], disposition_text)) + " |")
    if not snapshot["clusters"]:
        lines.extend(["", "No error issues returned for this window."])
    if comparison.get("newly_observed"):
        lines.extend(["", "Newly observed: " + ", ".join(map(_cell, comparison["newly_observed"]))])
    if comparison.get("not_observed"):
        lines.extend(["", "Not observed: " + ", ".join(map(_cell, comparison["not_observed"]))])
    lines.extend(["", "Sample versions identify one report, not all affected versions. "
                  "Use the Play issue and release-track data to verify a fix in the shipped version."])
    return "\n".join(lines) + "\n"


def persist_snapshot(directory: Path, snapshot: dict) -> Path:
    """Retain every distinct snapshot before replacing the current report."""
    history = directory / "history"
    history.mkdir(parents=True, exist_ok=True)
    payload = json_text(snapshot)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    stamp = snapshot["collected_at"].replace(":", "").replace("-", "")
    archive = history / f"{stamp}-{digest}.json"
    # Publish a complete file without replacing a historical snapshot. A failed
    # write leaves only a temporary file, never a truncated immutable archive.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=history,
                                     prefix=".", suffix=".tmp", delete=False) as stream:
        temporary_archive = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            try:
                os.link(temporary_archive, archive)
            except FileExistsError:
                if archive.read_text(encoding="utf-8") != payload:
                    raise ValueError(f"Existing snapshot differs from its content hash: {archive}")
        finally:
            temporary_archive.unlink(missing_ok=True)
    for name, text in (("current.json", payload), ("current.md", render_markdown(snapshot))):
        temporary = directory / f".{name}.{digest}.tmp"
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(directory / name)
    return archive
