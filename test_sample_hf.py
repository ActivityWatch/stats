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
