"""Offline structural/noise regression tests; fixtures contain no live reports."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crash_signatures import fallback_fingerprint, normalize_trace


FIXTURES = Path(__file__).parent / "fixtures" / "crashes"


def trace(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text()


def fingerprint(text: str) -> str:
    return fallback_fingerprint("CRASH", "", "", text)


def test_managed_npe_retains_discriminating_app_frame():
    assert normalize_trace(trace("managed-npe")) == {
        "causes": ["java.lang.NullPointerException"],
        "frames": [
            "net.activitywatch.android.watcher.ChromeWatcher.onAccessibilityEvent",
            "android.accessibilityservice.AccessibilityService$2.onAccessibilityEvent",
        ],
    }


def test_v1_fingerprint_contract_is_stable():
    # An intentional normalization change must bump the version, not silently
    # reinterpret historical reports under the existing fingerprint namespace.
    assert fingerprint(trace("managed-npe")) == (
        "fallback:v1:96a2fada8fc9d092cfcfc96cef2e37bc5bfb6fc383afb655d52632f0f58f234e"
    )


def test_nested_inflation_retains_cause_order_and_constructor():
    normalized = normalize_trace(trace("nested-inflation"))
    assert normalized["causes"] == [
        "java.lang.RuntimeException",
        "android.view.InflateException",
        "java.lang.UnsupportedOperationException",
    ]
    assert "android.widget.TextView.<init>" in normalized["frames"]
    reversed_causes = trace("nested-inflation").replace(
        "java.lang.RuntimeException", "java.lang.UnsupportedOperationException"
    ).replace("Caused by: java.lang.UnsupportedOperationException", "Caused by: java.lang.RuntimeException")
    assert fingerprint(reversed_causes) != fingerprint(trace("nested-inflation"))


def test_native_abort_retains_signal_and_jni_not_unsymbolicated_addresses():
    assert normalize_trace(trace("native-jni-abort")) == {
        "causes": ["SIGABRT"],
        "frames": [
            "abort",
            "Java_net_activitywatch_android_SyncInterface_syncBoth",
            "SyncInterface$syncBothAsync$2.invoke",
            "SyncInterface.performSyncAsync$lambda$4",
        ],
    }


def test_synthetic_rust_preserves_functions_not_build_hashes():
    assert normalize_trace(trace("rust-native-synthetic")) == {
        "causes": [],
        "frames": [
            "std::panicking::begin_panic",
            "aw_sync::sync::sync_run",
            "aw_sync::jni::sync_both",
        ],
    }


@pytest.mark.parametrize("fixture", [
    "managed-npe", "nested-inflation", "native-jni-abort", "rust-native-synthetic"
])
def test_incidental_noise_does_not_split_family(fixture):
    original = trace(fixture)
    changed = original.replace("[redacted", "[another-secret").replace("12345", "98989")
    changed = changed.replace("00:00:00.001", "23:59:59.999").replace(":76)", ":999)")
    changed = changed.replace("+260", "+0x100").replace("0000000000000030", "0000000098765432")
    changed = changed.replace("0123456789abcdef", "ffffffffffffffff")
    changed = changed.replace("/private/checkout", "/data/user/another-private-path")
    changed += "\nDevice: private-device\nversionCode: 99\nBuildId: deadbeef\n"
    assert fingerprint(changed) == fingerprint(original)


@pytest.mark.parametrize(("fixture", "before", "after"), [
    ("managed-npe", "NullPointerException", "IllegalStateException"),
    ("managed-npe", "ChromeWatcher.onAccessibilityEvent", "ChromeWatcher.onInterrupt"),
    ("native-jni-abort", "SIGABRT", "SIGSEGV"),
    ("native-jni-abort", "syncBoth+260", "syncPush+260"),
    ("rust-native-synthetic", "aw_sync::sync::sync_run", "aw_sync::sync::sync_push"),
])
def test_structural_changes_split_family(fixture, before, after):
    original = trace(fixture)
    assert fingerprint(original.replace(before, after)) != fingerprint(original)


def test_messages_and_malformed_lines_are_not_retained():
    private = "private@example.test /data/user/secret token=secret-value"
    text = f'''Exception in thread "{private}" java.lang.NullPointerException: {private}
    at net.activitywatch.android.MainActivity.onCreate (/private/project/MainActivity.kt:19)
    caused by arbitrary prose {private}
    at {private}
    #15 pc 0123 /private/libaw_sync.so ({private})
    #00 pc 4567 /private/base.apk (offset 0x1234)
    1: {private}
    Process: {private}
    https://{private}
    '''
    assert normalize_trace(text) == {
        "causes": ["java.lang.NullPointerException"],
        "frames": ["net.activitywatch.android.MainActivity.onCreate"],
    }
    assert "private" not in json.dumps(normalize_trace(text))


def test_native_frame_ignores_path_build_id_and_offset():
    first = "#00 pc 1234 /data/user/private/libaw_sync.so (aw_sync::sync::run+16) (BuildId: abcd)"
    second = "#00 pc abcdef /another/path/libaw_sync.so (aw_sync::sync::run+0x12) (BuildId: fedc)"
    assert normalize_trace(first) == {"causes": [], "frames": ["aw_sync::sync::run"]}
    assert fingerprint(first) == fingerprint(second)


def test_full_logcat_prefix_and_fatal_signal():
    text = "09-08 12:34:56.789 12345 12346 F libc : Fatal signal 6 (SIGABRT) in tid 12346 (private)"
    assert normalize_trace(text) == {"causes": ["SIGABRT"], "frames": []}


def test_fallback_uses_normalized_metadata_and_versioned_digest():
    first = fallback_fingerprint(
        "CRASH", "java.lang.NullPointerException: secret-one",
        "ChromeWatcher.onAccessibilityEvent (ChromeWatcher.kt:76)"
    )
    second = fallback_fingerprint(
        " crash ", "java.lang.NullPointerException: secret-two",
        "ChromeWatcher.onAccessibilityEvent (/private/ChromeWatcher.kt:99)"
    )
    assert first == second
    assert first.startswith("fallback:v1:")
    assert len(first.removeprefix("fallback:v1:")) == 64
    assert fallback_fingerprint("ANR", "java.lang.NullPointerException", "") != first


def test_api_anr_type_matches_filter_alias_and_does_not_become_unknown():
    api_type = fallback_fingerprint("APPLICATION_NOT_RESPONDING", "", "")
    assert api_type == fallback_fingerprint("ANR", "", "")
    assert api_type != fallback_fingerprint("UNKNOWN", "", "")


def test_empty_or_unsupported_reports_have_explicitly_heuristic_collision():
    assert normalize_trace("not a stack trace") == {"causes": [], "frames": []}
    assert fallback_fingerprint("CRASH", "secret-one", "path/one") == fallback_fingerprint(
        "CRASH", "secret-two", "path/two"
    )
