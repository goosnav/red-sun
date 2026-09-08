"""GUI model checks: settings persistence, the folder browser listing, run-folder placement.
Run: uv run --project app --group dev pytest"""

from pathlib import Path

import pytest
from PIL import Image

from red_sun import core, server


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "config_dir", lambda: tmp_path / "config")
    return tmp_path / "config"


def test_settings_persist_and_ignore_junk(config):
    assert server.load_fields() == server.defaults()          # nothing saved yet
    fields = {**server.defaults(), "palette": "win16", "dither": "pattern", "path": "/somewhere", "recursive": "on",
              "session": "secret-token", "for": "source", "evil": "x"}
    server.save_fields(fields)
    saved = server.load_fields()
    assert saved["palette"] == "win16" and saved["dither"] == "pattern" and saved["recursive"] == "on"
    assert "session" not in saved and "for" not in saved and "evil" not in saved
    (config / "settings.json").write_text("{ not json")
    assert server.load_fields() == server.defaults()          # corrupt file falls back to defaults


def test_form_defaults_round_trip():
    assert server.settings_from(server.defaults()) == core.Settings()


def test_folder_listing_skips_hidden_and_counts_images(tmp_path):
    (tmp_path / "b folder").mkdir()
    (tmp_path / "A").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "Photos Library.photoslibrary").mkdir()
    Image.new("RGB", (8, 8)).save(tmp_path / "photo.JPG")
    Image.new("RGB", (8, 8)).save(tmp_path / "scan.png")
    (tmp_path / ".DS_Store").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("x")
    listing = server.list_folder(tmp_path)
    assert [p.name for p in listing.folders] == ["A", "b folder"]
    assert [p.name for p in listing.images] == ["photo.JPG", "scan.png"]
    assert listing.more == 0 and listing.problem is None


def test_unreadable_folder_is_reported_not_raised(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        listing = server.list_folder(locked)
        assert listing.problem and listing.folders == []
    finally:
        locked.chmod(0o755)


def test_run_dir_is_created_next_to_the_source(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (8, 8)).save(photos / "a.png")
    first = server.create_run_dir(photos)
    second = server.create_run_dir(photos / "a.png")           # a single image: next to that image
    assert first == photos / "red-sun-run-001" and second == photos / "red-sun-run-002"
    assert first.is_dir() and second.is_dir()
    # a recursive scan of the photos folder must not pick up outputs from earlier runs
    Image.new("RGB", (8, 8)).save(first / "a_redsun_16c.png")
    assert [p.name for p in core.collect_files(photos, recursive=True)] == ["a.png"]


def test_start_dir_prefers_the_last_folder(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (8, 8)).save(photos / "a.png")
    assert server.start_dir({"path": str(photos / "a.png")}) == photos
    assert server.start_dir({"path": "", "out_dir": str(tmp_path)}) == tmp_path
    assert server.start_dir({"path": "/nowhere/at/all"}).is_dir()


def test_browse_page_renders_links(tmp_path):
    (tmp_path / "sub dir").mkdir()
    Image.new("RGB", (8, 8)).save(tmp_path / "im age.png")
    html = server.render_browse(tmp_path, "source")
    assert "sub+dir" in html and "Use this folder" in html and "1 image here" in html
    assert "im+age.png" in html and 'name="path"' in html
    html = server.render_browse(tmp_path, "out")
    assert 'name="out"' in html and "Images (" not in html
