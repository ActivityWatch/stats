# v0.14 launch minute-resolution samples

These CSVs are the untrimmed output of `sample_hf.py --interval 60 --until
2026-10-20T00:00:00Z` on Bob's host, collected at Erik's request. No rows were
coarsened, deduplicated or rewritten for submission. The sampler exited
successfully at its cutoff and was disabled afterward.

| File | Data rows | SHA-256 |
| --- | ---: | --- |
| `stats.csv` | 8,733 | `a9818798803d23b6e53eae60900a7ca545788bdcd1c5e5c517e7ce05695889b2` |
| `stats-assets.csv` | 18,055 | `af0237acd7f7c0fad1f70863bd920121e8995318f3cf547ce3bfa4bfc12e0065` |

First recorded snapshot: **2026-10-06 21:14:13 UTC**. Last change recorded:
**2026-10-19 23:57:03 UTC**. The last successful poll was 2026-10-20 00:00:04 UTC
with no changed rows. Rows are change-only, not proof of a successful poll every
minute. Two transient API/connection failures appear in the retained service
journal and retried on the next interval.

## Bounded reach readout

Using `analyze_stats.py`'s `_asset_series`, `_asset_meta` and
`version_platform_totals` on the high-frequency per-asset file, after excluding
`collect_stats.is_updater_asset` assets:

- Aggregate downloads: **1,194,472 → 1,232,764**, a net **38,292**.
- GitHub stars: **19,082 → 19,293**, a net **211** (stars can also decrease).
- Download increments by platform: Windows **18,933**, macOS **10,816**, Linux
  **8,542**, unknown **1**. Sum: **38,292**, exactly matching the aggregate delta.
- v0.14.0 increments in this window: **12,355** (Windows 5,732; macOS 3,540;
  Linux 3,083).
- v0.14.1 downloads: **21,265** (Windows 11,052; macOS 6,574; Linux 3,639).
  This release first appears on October 10, after sampling began; its assets'
  pre-window baseline is zero, not their first positive count.

For reconstruction, pivot each `(tag, asset)` counter over time and forward-fill
it, using the existing analysis helpers. Subtract the initial counter for assets
present in the first snapshot. For assets added during the window, count their
full final counter from a zero baseline; subtracting the first positive sample
would lose initial downloads. Retain updater rows in the raw file, but exclude
them from download reach: `latest.json` requests and updater artifacts are not
new-user downloads.

## Attribution and conversion limits

The committed `data/launch-events.csv` contains only the October 6 GitHub release
at 13:38 UTC and the approximately 17:00 UTC Discord announcement. **Both precede
the first high-frequency snapshot.** The later X/Reddit, Product Hunt and HN
posts named in the sampling plan do not have verified event timestamps/URLs in
that file. Consequently this dataset cannot provide a measured per-post lift or
rank those channels. Planned posts are not evidence that a post happened. Add
verified event records before attempting channel-window comparisons, and do not
interpret overlapping windows as causal attribution.

These counters measure asset requests and net repository stars, **not unique
people, successful installs, active users or paid conversions**. This dataset has
no channel exposure denominator or subscriber linkage. AW Pro conversion must be
reported separately from the invoice-linked/source-attributed funnel; no
conversion rate can be calculated from these download totals. In particular,
v0.14.1 downloads are not evidence of a successful Windows install or upgrade.
