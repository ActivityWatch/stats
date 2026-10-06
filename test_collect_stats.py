from collect_stats import is_updater_asset, platform


def test_updater_assets_are_excluded_from_downloads():
    assert is_updater_asset("latest.json")
    assert is_updater_asset("activitywatch-tauri-0.14.0-darwin-aarch64.app.tar.gz")
    assert is_updater_asset("activitywatch-tauri-0.14.0-linux-aarch64.AppImage.sig")
    assert not is_updater_asset("activitywatch-v0.14.0-windows-x86_64-setup.exe")
    assert not is_updater_asset("activitywatch-tauri-0.14.0-linux-x86_64.AppImage")
    assert not is_updater_asset("activitywatch-v0.14.0-macos-arm64.dmg")


def test_platform():
    assert platform("activitywatch-tauri-0.14.0-darwin-aarch64.app.tar.gz") == "macos"
    assert platform("latest.json") == "unknown"
