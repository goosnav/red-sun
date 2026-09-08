"""Multipart parser checks (the one hand-written protocol piece). Run: uv run --project app --group dev pytest"""

import io
from pathlib import Path

import pytest

from red_sun import server


def body(parts: list[tuple[str, str | None, bytes]], boundary=b"XyZ") -> bytes:
    out = b""
    for name, filename, data in parts:
        out += b"--" + boundary + b"\r\n"
        disposition = f'Content-Disposition: form-data; name="{name}"' + (f'; filename="{filename}"' if filename is not None else "")
        out += disposition.encode() + (b"\r\nContent-Type: application/octet-stream" if filename else b"") + b"\r\n\r\n" + data + b"\r\n"
    return out + b"--" + boundary + b"--\r\n"


class Trickle(io.BytesIO):
    """Returns at most n bytes per read so boundaries straddle reads."""

    def __init__(self, data: bytes, n: int):
        super().__init__(data)
        self.n = n

    def read(self, size=-1):
        return super().read(min(size, self.n) if size and size > 0 else self.n)


@pytest.mark.parametrize("chunk", [1, 3, 7, 1 << 20])
def test_multipart_streams_files_and_fields(tmp_path: Path, chunk: int):
    binary = b"\r\n--XyZ-lookalike\r\n" + bytes(range(256)) * 40 + b"\r\n"   # boundary-like bytes inside a file
    data = body([
        ("session", None, b"tok"),
        ("path", None, "Réd/ページ".encode()),
        ("files", "a.png", b"PNG1"),
        ("files", "sub/dir/a.png", binary),          # webkitdirectory-style relative name, same base name
        ("files", "", b""),                          # empty <input type=file>
        ("folder", "C:\\photos\\win.jpg", b"JPG"),   # old-style full path
    ])
    fields, files = server.parse_multipart(Trickle(data, chunk), b"XyZ", len(data), tmp_path)
    assert fields == {"session": "tok", "path": "Réd/ページ"}
    assert [p.name for p in files] == ["a.png", "a-2.png", "win.jpg"]
    assert (tmp_path / "a.png").read_bytes() == b"PNG1"
    assert (tmp_path / "a-2.png").read_bytes() == binary
    assert (tmp_path / "win.jpg").read_bytes() == b"JPG"
    assert not (tmp_path / "upload").exists()


def test_multipart_rejects_garbage_and_huge_text(tmp_path: Path):
    with pytest.raises(ValueError):
        server.parse_multipart(io.BytesIO(b"no boundary here"), b"XyZ", 16, tmp_path)
    huge = body([("path", None, b"x" * (server.MAX_TEXT_BYTES + 10))])
    with pytest.raises(ValueError):
        server.parse_multipart(io.BytesIO(huge), b"XyZ", len(huge), tmp_path)


def test_form_defaults_round_trip():
    s = server.settings_from(server.defaults())
    assert s == server.core.Settings()
