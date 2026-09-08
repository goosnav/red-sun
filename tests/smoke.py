#!/usr/bin/env python3
"""End-to-end smoke test: real server, real HTTP, real files. Run: uv run --project app python tests/smoke.py"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "src"))

from PIL import Image  # noqa: E402

from red_sun import server  # noqa: E402


def main() -> int:
    config = Path(tempfile.mkdtemp(prefix="red-sun-smoke-config-"))
    server.config_dir = lambda: config  # keep the smoke's settings out of the real config folder
    srv = server.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    same_origin = {"Origin": base, "Sec-Fetch-Site": "same-origin"}

    def get(path: str):
        return urllib.request.urlopen(base + path, timeout=10)

    def post(path: str, fields: dict[str, str]):
        req = urllib.request.Request(base + path, data=urllib.parse.urlencode(fields).encode(), method="POST", headers=same_origin)
        return urllib.request.urlopen(req, timeout=10)

    assert get("/health/ready").status == 200, "readiness"
    home = get("/").read().decode()
    assert "<h1>Red Sun</h1>" in home and 'name="session"' in home and 'formaction="/choose"' in home
    assert get("/static/style.css").headers["Content-Type"] == "text/css"
    assert get("/favicon.ico").status == 200

    try:
        urllib.request.urlopen(urllib.request.Request(base + "/jobs", data=b"path=x", method="POST"), timeout=10)
        raise AssertionError("POST without the session token must be refused")
    except urllib.error.HTTPError as err:
        assert err.code == 403, err.code

    with tempfile.TemporaryDirectory() as tmp:
        photos = Path(tmp) / "my photos"
        (photos / "sub").mkdir(parents=True)
        Image.new("RGB", (400, 300), (200, 30, 30)).save(photos / "red.png")
        Image.new("L", (400, 300), 90).save(photos / "gray.jpg")
        Image.new("RGB", (400, 300), (30, 30, 200)).save(photos / "sub" / "blue.webp")

        # Choose folder…: the form is remembered, the OS dialog runs (stubbed here), the answer lands in the form
        if sys.platform == "darwin":  # the real AppleScript must at least compile and run its non-dialog parts
            probe = subprocess.run(["osascript", "-e", "activate", "-e", 'return POSIX path of (POSIX file "/tmp" as alias)'], capture_output=True, text=True, timeout=30)
            assert probe.returncode == 0 and probe.stdout.strip().endswith("tmp/"), probe
        calls = []
        server.native_pick = lambda kind, start: (calls.append((kind, start)), (str(photos), None))[1]
        fields = {**server.defaults(), "session": server.SESSION, "for": "source", "palette": "win16", "dither": "pattern"}
        resp = post("/choose", fields)                                  # 303 followed as GET /
        chosen = resp.read().decode()
        assert calls == [("source", Path.home() / "Pictures")] or calls[0][0] == "source"
        assert str(photos) in chosen and "2 images in this folder" in chosen
        assert 'value="win16" selected' in chosen and 'value="pattern" selected' in chosen, "settings must survive the round trip"
        server.native_pick = lambda kind, start: ("", None)             # cancelled: nothing changes
        assert str(photos) in post("/choose", {**fields, "path": str(photos)}).read().decode()

        # process with the saved settings; default output = a numbered folder next to the pictures
        fields = {**server.load_fields(), "session": server.SESSION, "recursive": "on", "min_output_width": "800"}
        job_url = post("/jobs", fields).url
        assert "/jobs/" in job_url, job_url
        for _ in range(150):
            page = urllib.request.urlopen(job_url, timeout=10).read().decode()
            if "Finished" in page:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("batch never finished")
        assert "3 of 3 images saved" in page, page
        run = photos / "red-sun-run-001"
        names = sorted(p.name for p in run.iterdir())
        assert names == ["blue_redsun_win16-pattern.png", "gray_redsun_bw-pattern.png", "red_redsun_win16-pattern.png", "settings.json"], names
        with Image.open(run / "red_redsun_win16-pattern.png") as im:
            assert im.mode == "P" and im.size == (800, 600), (im.mode, im.size)   # 400 px source x2
        assert urllib.request.urlopen(job_url + "/out/0", timeout=10).headers["Content-Type"] == "image/png"
        with Image.open(urllib.request.urlopen(job_url + "/out/0?crop=1", timeout=10)) as crop:
            assert crop.size == (480, 320), crop.size
        assert 'width="800" height="600"' in urllib.request.urlopen(job_url + "/view/0", timeout=10).read().decode()
        try:
            urllib.request.urlopen(job_url + "/out/99", timeout=10)
            raise AssertionError("out-of-range output must 404")
        except urllib.error.HTTPError as err:
            assert err.code == 404

        # a second run gets the next number and does not re-process the first run's outputs
        job2 = post("/jobs", fields).url
        for _ in range(150):
            page = urllib.request.urlopen(job2, timeout=10).read().decode()
            if "Finished" in page:
                break
            time.sleep(0.2)
        assert "3 of 3 images saved" in page and str(photos / "red-sun-run-002") in page, page

        # settings persisted on disk and restored by a fresh GET /
        assert (config / "settings.json").exists()
        restored = get("/").read().decode()
        assert 'value="win16" selected' in restored and str(photos) in restored

        quit_req = urllib.request.Request(base + "/quit", data=urllib.parse.urlencode({"session": server.SESSION}).encode(), method="POST", headers=same_origin)
        assert b"has quit" in urllib.request.urlopen(quit_req, timeout=10).read()
    thread.join(timeout=10)
    assert not thread.is_alive(), "server did not stop after Quit"
    srv.server_close()
    shutil.rmtree(config, ignore_errors=True)
    print(f"SMOKE OK ({base})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
