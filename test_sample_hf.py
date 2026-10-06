import csv

import sample_hf


class FakeClient:
    def __init__(self, assets, stars=10):
        self._assets = assets
        self._stars = stars

    def assets(self):
        return [dict(a) for a in self._assets]

    def stars(self):
        return self._stars


def _rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_only_changes_are_appended(tmp_path, monkeypatch):
    monkeypatch.setattr(sample_hf, "STATS_CSV", str(tmp_path / "stats.csv"))
    monkeypatch.setattr(sample_hf, "ASSETS_CSV", str(tmp_path / "stats-assets.csv"))
    assets = [
        {"tag": "v0.14.0", "asset": "setup.exe", "platform": "windows", "downloads": 5},
        {
            "tag": "v0.14.0",
            "asset": "latest.json",
            "platform": "unknown",
            "downloads": 50,
        },
    ]
    client = FakeClient(assets)
    state = sample_hf.load_state()
    assert sample_hf.sample_once(client, state) == (2, 1)
    assert sample_hf.sample_once(client, state) == (0, 0)  # nothing changed
    assets[0]["downloads"] = 6
    assert sample_hf.sample_once(client, state) == (1, 1)
    stats = _rows(sample_hf.STATS_CSV)
    # updater polls (latest.json) are not counted as downloads
    assert [r["downloads"] for r in stats] == ["5", "6"]
    # a restart resumes from the files
    assert sample_hf.sample_once(client, sample_hf.load_state()) == (0, 0)


def test_trim_keeps_last_row_per_bucket(tmp_path, monkeypatch):
    path = tmp_path / "stats.csv"
    monkeypatch.setattr(sample_hf, "STATS_CSV", str(path))
    monkeypatch.setattr(sample_hf, "ASSETS_CSV", str(tmp_path / "missing.csv"))
    path.write_text(
        "timestamp,downloads,stars\n"
        "2026-10-07T10:00:10+00:00,1,1\n"
        "2026-10-07T10:05:00+00:00,2,1\n"
        "2026-10-07T10:59:59+00:00,3,1\n"
        "2026-10-07T11:00:01+00:00,4,1\n"
    )
    sample_hf.trim(60)
    assert [r["downloads"] for r in _rows(path)] == ["3", "4"]


class FakeResponse:
    def __init__(self, status, data=None, etag="", next_url=None):
        self.status_code = status
        self._data = data
        self.headers = {"ETag": etag} if etag else {}
        self.links = {"next": {"url": next_url}} if next_url else {}

    def json(self):
        return self._data

    def raise_for_status(self):
        assert self.status_code < 400


def test_conditional_client_caches_pages_and_304s():
    page1 = "https://api.github.com" + sample_hf.RELEASES_PATH
    page2 = page1 + "&page=2"

    def rel(tag, n):
        return {
            "tag_name": tag,
            "assets": [{"name": f"{tag}-setup.exe", "download_count": n}],
        }

    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, (headers or {}).get("If-None-Match")))
        if (headers or {}).get("If-None-Match"):
            return FakeResponse(304)
        if url == page1:
            draft = {**rel("v3", 9), "draft": True}
            return FakeResponse(200, [draft, rel("v2", 5)], etag="e1", next_url=page2)
        return FakeResponse(200, [rel("v1", 3)], etag="e2")

    client = sample_hf.ConditionalClient(None)
    client.session.get = fake_get  # type: ignore[method-assign]
    first = client.assets()
    second = client.assets()  # both pages answered with 304, served from cache
    assert first == second
    assert {(a["tag"], a["downloads"]) for a in second} == {("v2", 5), ("v1", 3)}
    assert calls[2:] == [(page1, "e1"), (page2, "e2")]


def test_trim_keeps_each_asset_series(tmp_path, monkeypatch):
    path = tmp_path / "stats-assets.csv"
    monkeypatch.setattr(sample_hf, "STATS_CSV", str(tmp_path / "missing.csv"))
    monkeypatch.setattr(sample_hf, "ASSETS_CSV", str(path))
    path.write_text(
        "timestamp,tag,asset,platform,downloads\n"
        "2026-10-07T10:01:00+00:00,v1,a.exe,windows,1\n"
        "2026-10-07T10:02:00+00:00,v1,b.dmg,macos,7\n"
        "2026-10-07T10:30:00+00:00,v1,a.exe,windows,2\n"
    )
    sample_hf.trim(60)
    assert [(r["asset"], r["downloads"]) for r in _rows(path)] == [
        ("b.dmg", "7"),
        ("a.exe", "2"),
    ]


def test_lock_excludes_a_second_holder(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(sample_hf, "HF_DIR", str(tmp_path))
    monkeypatch.setattr(sample_hf, "LOCK_PATH", str(tmp_path / ".lock"))
    with sample_hf.exclusive_lock():
        with open(sample_hf.LOCK_PATH, "a+") as other:
            assert not sample_hf._try_lock(other)
    with pytest.raises(SystemExit):
        with sample_hf.exclusive_lock():
            with sample_hf.exclusive_lock():
                pass
