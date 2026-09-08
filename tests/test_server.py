"""GUI model checks: settings persistence, the OS folder dialog wrapper, run-folder placement.
Run: uv run --project app --group dev pytest"""

import subprocess
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


def test_dialog_commands_are_explicit_argv_and_quote_paths(monkeypatch):
    weird = Path('/tmp/it\'s "quoted" \\ odd')
    monkeypatch.setattr(server.platform, "system", lambda: "Darwin")
    cmd = server.dialog_command("source", weird)
    assert cmd[0] == "osascript" and "choose folder" in cmd[2] and 'POSIX file "/tmp/it\'s \\"quoted\\" \\\\ odd"' in cmd[2]
    assert "activate" in cmd[2]
    assert 'choose file of type {"public.image"}' in server.dialog_command("file", weird)[2]
    monkeypatch.setattr(server.platform, "system", lambda: "Windows")
    cmd = server.dialog_command("out", weird)
    assert cmd[:2] == ["powershell", "-NoProfile"] and "FolderBrowserDialog" in cmd[-1] and "it''s" in cmd[-1]
    assert "OpenFileDialog" in server.dialog_command("file", weird)[-1]
    monkeypatch.setattr(server.platform, "system", lambda: "Linux")
    monkeypatch.setattr(server.shutil, "which", lambda name: "/usr/bin/zenity")
    assert server.dialog_command("source", weird)[-1] == "--directory"
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    assert server.dialog_command("source", weird)[0] == "kdialog"


def test_native_pick_handles_choice_cancel_and_missing_tool(monkeypatch, tmp_path):
    def fake_run(argv, **kwargs):
        assert isinstance(argv, list) and kwargs.get("timeout")
        return subprocess.CompletedProcess(argv, 0, stdout="/Users/me/Pictures/scans/\n", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)
    assert server.native_pick("source", tmp_path) == ("/Users/me/Pictures/scans", None)

    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "User canceled."))
    assert server.native_pick("source", tmp_path) == ("", None)

    def missing(*a, **k):
        raise FileNotFoundError("zenity")

    monkeypatch.setattr(server.subprocess, "run", missing)
    chosen, problem = server.native_pick("source", tmp_path)
    assert chosen is None and "zenity" in problem

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(a, 1)

    monkeypatch.setattr(server.subprocess, "run", slow)
    assert server.native_pick("out", tmp_path / "nope") == ("", None)   # a missing start folder is fine too


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
