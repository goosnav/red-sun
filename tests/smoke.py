#!/usr/bin/env python3
"""End-to-end smoke test: real server, real HTTP, real files. Run: uv run --project app python tests/smoke.py"""

from __future__ import annotations

import io
import shutil
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

from red_sun import core, server  # noqa: E402


def multipart(fields: dict[str, str], files: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    boundary = "----RedSunSmoke"
    out = b""
    for name, value in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
    for name, filename, data in files:
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def png_bytes(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), color).save(buf, "PNG")
    return buf.getvalue()


def main() -> int:
    exports = Path(tempfile.mkdtemp(prefix="red-sun-smoke-exports-"))
    core.exports_root = lambda: exports  # keep the numbered-run default out of the real Pictures folder
    srv = server.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    same_origin = {"Origin": base, "Sec-Fetch-Site": "same-origin"}

    def get(path: str):
        return urllib.request.urlopen(base + path, timeout=10)

    assert get("/health/ready").status == 200, "readiness"
    home = get("/").read().decode()
    assert "<h1>Red Sun</h1>" in home and 'name="session"' in home and 'enctype="multipart/form-data"' in home
    assert 'webkitdirectory' in home and "red-sun-run-" in home
    assert get("/static/style.css").headers["Content-Type"] == "text/css"
    assert get("/favicon.ico").status == 200

    try:
        urllib.request.urlopen(urllib.request.Request(base + "/jobs", data=b"path=x", method="POST"), timeout=10)
        raise AssertionError("POST without the session token must be refused")
    except urllib.error.HTTPError as err:
        assert err.code == 403, err.code

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "typed"
        src.mkdir()
        Image.new("L", (400, 300), 90).save(src / "gray.jpg")
        out_root = Path(tmp) / "exports"
        fields = {
            "session": server.SESSION, "path": str(src), "mode": "auto", "palette": "paint", "colors": "16",
            "dither": "pattern", "dither_strength": "60", "sharpen": "on", "contrast": "on",
            "min_output_width": "800", "fmt": "png", "out_dir": str(out_root / "red-sun-run-001"),
        }
        uploads = [("files", "red.png", png_bytes((200, 30, 30))), ("folder", "sub/blue.png", png_bytes((30, 30, 200)))]
        body, ctype = multipart(fields, uploads)
        req = urllib.request.Request(base + "/jobs", data=body, method="POST", headers={**same_origin, "Content-Type": ctype})
        job_url = urllib.request.urlopen(req, timeout=10).url  # 303 followed as GET
        assert "/jobs/" in job_url, job_url
        for _ in range(150):
            page = urllib.request.urlopen(job_url, timeout=10).read().decode()
            if "Finished" in page:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("batch never finished")
        assert "3 of 3 images saved" in page, page
        run = out_root / "red-sun-run-001"
        names = sorted(p.name for p in run.iterdir())
        assert names == ["blue_redsun_paint28-pattern.png", "gray_redsun_bw-pattern.png", "red_redsun_paint28-pattern.png", "settings.json"], names
        with Image.open(run / "red_redsun_paint28-pattern.png") as im:
            assert im.mode == "P" and im.size == (800, 600), (im.mode, im.size)   # 400 px source x2
        assert urllib.request.urlopen(job_url + "/out/0", timeout=10).headers["Content-Type"] == "image/png"
        with Image.open(urllib.request.urlopen(job_url + "/out/0?crop=1", timeout=10)) as crop:
            assert crop.size == (480, 320), crop.size
        view = urllib.request.urlopen(job_url + "/view/0", timeout=10).read().decode()
        assert 'width="800" height="600"' in view
        try:
            urllib.request.urlopen(job_url + "/out/99", timeout=10)
            raise AssertionError("out-of-range output must 404")
        except urllib.error.HTTPError as err:
            assert err.code == 404
        assert not list(Path(tempfile.gettempdir()).glob("red-sun-upload-*")), "upload temp dir must be cleaned"

        # default (numbered) output folder: create a second run without out_dir
        fields2 = {k: v for k, v in fields.items() if k != "out_dir"}
        body, ctype = multipart(fields2, uploads[:1])
        req = urllib.request.Request(base + "/jobs", data=body, method="POST", headers={**same_origin, "Content-Type": ctype})
        job2 = urllib.request.urlopen(req, timeout=10).url
        for _ in range(150):
            page = urllib.request.urlopen(job2, timeout=10).read().decode()
            if "Finished" in page:
                break
            time.sleep(0.2)
        assert str(exports / "red-sun-run-001") in page, page
        assert (exports / "red-sun-run-001" / "red_redsun_paint28-pattern.png").exists()

        quit_req = urllib.request.Request(base + "/quit", data=urllib.parse.urlencode({"session": server.SESSION}).encode(), method="POST", headers=same_origin)
        assert b"has quit" in urllib.request.urlopen(quit_req, timeout=10).read()
    thread.join(timeout=10)
    assert not thread.is_alive(), "server did not stop after Quit"
    srv.server_close()
    shutil.rmtree(exports, ignore_errors=True)
    print(f"SMOKE OK ({base})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
