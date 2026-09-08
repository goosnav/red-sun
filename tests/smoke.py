#!/usr/bin/env python3
"""End-to-end smoke test: real server, real HTTP, real files. Run: uv run --project app python tests/smoke.py"""

from __future__ import annotations

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
    srv = server.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def get(path: str):
        return urllib.request.urlopen(base + path, timeout=10)

    assert get("/health/ready").status == 200, "readiness"
    home = get("/").read().decode()
    assert "<h1>Red Sun</h1>" in home and 'name="session"' in home and "<title>Red Sun</title>" in home
    assert get("/static/style.css").headers["Content-Type"] == "text/css"
    assert get("/favicon.ico").status == 200

    try:
        urllib.request.urlopen(urllib.request.Request(base + "/jobs", data=b"path=x", method="POST"), timeout=10)
        raise AssertionError("POST without the session token must be refused")
    except urllib.error.HTTPError as err:
        assert err.code == 403, err.code

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in"
        src.mkdir()
        for name, color in (("a.png", (200, 30, 30)), ("b.jpg", (30, 30, 200)), ("c.webp", (90, 90, 90))):
            Image.new("RGB", (400, 300), color).save(src / name)
        form = {
            "session": server.SESSION, "path": str(src), "mode": "auto", "palette": "paint", "colors": "16",
            "despeckle": "on", "contrast": "on", "grid_width": "100", "output_width": "300", "fmt": "png",
        }
        req = urllib.request.Request(
            base + "/jobs", data=urllib.parse.urlencode(form).encode(), method="POST",
            headers={"Origin": base, "Sec-Fetch-Site": "same-origin"},
        )
        job_url = urllib.request.urlopen(req, timeout=10).url  # 303 followed as GET
        assert "/jobs/" in job_url, job_url
        for _ in range(150):
            body = urllib.request.urlopen(job_url, timeout=10).read().decode()
            if "Finished" in body:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("batch never finished")
        assert "3 of 3 images saved" in body, body
        outputs = sorted((src / "red_sun").glob("*.png"))
        assert [p.name for p in outputs] == ["a_redsun_paint28.png", "b_redsun_paint28.png", "c_redsun_bw.png"], outputs
        with Image.open(outputs[0]) as im:
            assert im.mode == "P" and im.size == (300, 225), (im.mode, im.size)
        assert urllib.request.urlopen(job_url + "/out/0", timeout=10).headers["Content-Type"] == "image/png"
        with Image.open(urllib.request.urlopen(job_url + "/out/0?preview=1", timeout=10)) as preview:
            assert preview.size == (100, 75), preview.size
        try:
            urllib.request.urlopen(job_url + "/out/99", timeout=10)
            raise AssertionError("out-of-range output must 404")
        except urllib.error.HTTPError as err:
            assert err.code == 404

        quit_req = urllib.request.Request(
            base + "/quit", data=urllib.parse.urlencode({"session": server.SESSION}).encode(), method="POST",
            headers={"Origin": base, "Sec-Fetch-Site": "same-origin"},
        )
        assert b"has quit" in urllib.request.urlopen(quit_req, timeout=10).read()
    thread.join(timeout=10)
    assert not thread.is_alive(), "server did not stop after Quit"
    srv.server_close()
    print(f"SMOKE OK ({base})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
