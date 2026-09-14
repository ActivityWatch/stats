# Crash signature fixtures

These are small structural excerpts, not production reports. Messages, device
details, process/thread IDs, timestamps, and addresses are replaced or omitted.
Only exception classes and code symbols relevant to extraction remain.

| Fixture | Evidence |
| --- | --- |
| `managed-npe.txt` | [ActivityWatch/aw-android#185](https://github.com/ActivityWatch/aw-android/issues/185): ChromeWatcher managed NPE; source excerpt has no message, so a redaction placeholder was added to exercise message removal. |
| `nested-inflation.txt` | [ActivityWatch/aw-android#210](https://github.com/ActivityWatch/aw-android/issues/210): RuntimeException, nested InflateException, and UnsupportedOperationException. |
| `native-jni-abort.txt` | [ActivityWatch/aw-android#220](https://github.com/ActivityWatch/aw-android/issues/220): SIGABRT in the libaw_sync path with `syncBoth` JNI evidence; redundant unsymbolicated frames omitted. This report does **not** establish a Rust panic. |
| `rust-native-synthetic.txt` | Explicitly synthetic Rust backtrace, using illustrative `aw_sync` symbols; not a captured ActivityWatch failure or evidence of a particular bug. |

Sources were read on 2026-09-08. The normalized fingerprint is a **heuristic
fallback** only. Play issue IDs remain primary. Google's
[ErrorReport reference](https://developers.google.com/play/developer/reporting/reference/rest/v1beta1/vitals.errors.reports#ErrorReport)
describes `reportText` as human-readable and warns that its format can change.
Unsupported and unsymbolicated lines are discarded rather than preserved as raw
text. Complex demangled C++/Rust symbols are outside this extractor's scope.
