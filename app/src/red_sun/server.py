"""Red Sun local GUI: plain HTML forms, zero JavaScript, bound to 127.0.0.1 only.

Every page is rendered here from the same core the CLI uses. State-changing
requests need the per-launch session token (a hidden form field) and a
same-origin check, so a malicious website cannot drive the app. Images arrive
through ordinary browser file/folder inputs (multipart, streamed to a temp
directory) or as a typed path; nothing opens outside the browser except the
output folder on request.
"""

from __future__ import annotations

import html
import io
import json
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from . import __version__, core

STATIC = Path(__file__).resolve().parents[2] / "static"
STATIC_FILES = {"style.css": "text/css", "icon.png": "image/png", "favicon.ico": "image/x-icon"}
SESSION = secrets.token_urlsafe(24)
MAX_TEXT_BYTES = 65536
CROP = (480, 320)

e = html.escape


@dataclass
class Job:
    id: str
    settings: core.Settings
    files: list[Path]
    out_dir: Path
    upload_dir: Path | None = None
    results: list[core.Result] = field(default_factory=list)
    finished: bool = False
    error: str | None = None

    def outputs(self) -> list[tuple[core.Result, Path]]:
        return [(r, p) for r in self.results for p in r.outputs]


JOBS: dict[str, Job] = {}
LOCK = threading.Lock()


# ---------------------------------------------------------------- multipart (streamed, stdlib only)

DISPOSITION = re.compile(r'name="([^"]*)"(?:;\s*filename="([^"]*)")?')


def parse_multipart(stream, boundary: bytes, length: int, save_dir: Path) -> tuple[dict[str, str], list[Path]]:
    """Stream a multipart/form-data body: text fields -> dict, file parts -> files under save_dir."""
    fields: dict[str, str] = {}
    files: list[Path] = []
    delim = b"\r\n--" + boundary
    buf = bytearray(b"\r\n")  # so the very first "--boundary" matches the CRLF-prefixed delimiter
    remaining = length

    def fill() -> bool:
        nonlocal remaining
        if remaining <= 0:
            return False
        chunk = stream.read(min(1 << 20, remaining))
        if not chunk:
            remaining = 0
            return False
        remaining -= len(chunk)
        buf.extend(chunk)
        return True

    def find_delim() -> int:
        """Index of a real delimiter (boundary followed by CRLF or --), -1 if none, -2 if undecidable yet."""
        pos = 0
        while (j := buf.find(delim, pos)) >= 0:
            end = j + len(delim)
            if len(buf) < end + 2:
                return -2
            if buf[end:end + 2] in (b"\r\n", b"--"):
                return j
            pos = j + 1
        return -1

    def consume_until_delim(sink, cap: int | None) -> None:
        keep = len(delim) + 1  # a boundary (plus its 2-byte lookahead) may straddle two reads
        while (j := find_delim()) < 0:
            if j == -1 and len(buf) > keep:
                sink.write(bytes(buf[:-keep]))
                del buf[:-keep]
            if cap is not None and sink.tell() > cap:
                raise ValueError("form field too large")
            if not fill():
                raise ValueError("multipart part is unterminated")
        sink.write(bytes(buf[:j]))
        del buf[: j + len(delim)]
        if cap is not None and sink.tell() > cap:
            raise ValueError("form field too large")

    consume_until_delim(io.BytesIO(), MAX_TEXT_BYTES)  # preamble (normally empty)
    while True:
        while len(buf) < 2 and fill():
            pass
        if buf.startswith(b"--"):
            break
        while (h := buf.find(b"\r\n\r\n")) < 0:
            if len(buf) > MAX_TEXT_BYTES or not fill():
                raise ValueError("multipart part headers are malformed")
        headers = bytes(buf[2:h]).decode("utf-8", "replace")
        del buf[: h + 4]
        match = DISPOSITION.search(headers)
        name, filename = (match.group(1), match.group(2)) if match else ("", None)
        if filename:
            safe = Path(filename.replace("\\", "/")).name or "upload"
            target, k = save_dir / safe, 2
            while target.exists():
                target, k = save_dir / f"{Path(safe).stem}-{k}{Path(safe).suffix}", k + 1
            with target.open("wb") as sink:
                consume_until_delim(sink, None)
            if target.stat().st_size:
                files.append(target)
            else:
                target.unlink()
        else:
            sink = io.BytesIO()
            consume_until_delim(sink, MAX_TEXT_BYTES)
            if filename is None:  # filename="" is an empty <input type=file>, not a text field
                fields[name] = sink.getvalue().decode("utf-8", "replace")
    return fields, files


# ---------------------------------------------------------------- form model

def defaults() -> dict[str, str]:
    s = core.Settings()
    return {
        "path": "", "out_dir": "", "mode": s.mode, "palette": s.palette, "colors": str(s.colors),
        "dither": s.dither, "dither_strength": str(s.dither_strength), "sharpen": "on", "contrast": "on",
        "threshold": "", "matte": s.matte, "grid_width": "", "min_output_width": str(s.min_output_width), "fmt": s.fmt,
    }


def settings_from(f: dict[str, str]) -> core.Settings:
    def num(name: str, default):
        value = f.get(name, "").strip()
        return int(value) if value else default

    s = core.Settings(
        mode=f.get("mode", "auto"), palette=f.get("palette", "adaptive"), colors=num("colors", 16),
        dither=f.get("dither", "none"), dither_strength=num("dither_strength", 60),
        sharpen="sharpen" in f, despeckle="despeckle" in f, contrast="contrast" in f,
        threshold=num("threshold", None), matte=f.get("matte", "white"), grid_width=num("grid_width", None),
        min_output_width=num("min_output_width", 3200), fmt=f.get("fmt", "png"),
    )
    s.validate()
    return s


def clean_path(text: str) -> Path:
    return Path(text.strip().strip('"\'')).expanduser()


# ---------------------------------------------------------------- rendering

def page(title: str, body: str, refresh: int | None = None) -> str:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="stylesheet" href="/static/style.css">
<link rel="icon" href="/favicon.ico">
{meta}
</head>
<body>
<header>
<p><img src="/static/icon.png" alt="" width="64" height="64"></p>
<h1>Red Sun</h1>
<p>Turn any photo into a sharp, color-indexed MS Paint style bitmap. Every pixel is forced into a small exact palette at full resolution, so edges stay hard no matter how far you zoom.</p>
</header>
<main>
{body}
</main>
<hr>
<footer>
<p>Red Sun {e(__version__)}. Each run is saved to a new numbered folder under <code>{e(str(core.exports_root()))}</code> unless you choose another. Original files are never modified.</p>
<form method="post" action="/quit">
<input type="hidden" name="session" value="{SESSION}">
<button>Quit Red Sun</button>
</form>
</footer>
</body>
</html>
"""


def option(value: str, label: str, current: str) -> str:
    sel = " selected" if value == current else ""
    return f'<option value="{value}"{sel}>{e(label)}</option>'


def checked(f: dict[str, str], name: str) -> str:
    return " checked" if name in f else ""


def render_form(f: dict[str, str], problem: str | None = None) -> str:
    alert = f"<p><strong>Problem:</strong> {e(problem)}</p>" if problem else ""
    next_run = core.next_run_dir()
    body = f"""
{alert}
<form method="post" action="/jobs" enctype="multipart/form-data">
<input type="hidden" name="session" value="{SESSION}">

<fieldset>
<legend>Source</legend>
<p>
<label for="files">Choose images</label><br>
<input id="files" name="files" type="file" multiple accept=".png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.gif,image/*">
</p>
<p>
<label for="folder">Or choose a whole folder</label><br>
<input id="folder" name="folder" type="file" webkitdirectory multiple>
</p>
<p>
<label for="path">Or type a file or folder path (fastest for big folders)</label><br>
<input id="path" name="path" type="text" size="60" value="{e(f.get('path', ''))}" placeholder="/Users/you/Pictures/scans">
<label><input type="checkbox" name="recursive"{checked(f, 'recursive')}> include subfolders</label>
</p>
</fieldset>

<fieldset>
<legend>Look</legend>
<p>
<label for="mode">Mode</label>
<select id="mode" name="mode">
{option('auto', 'Auto (black & white for grayscale sources)', f.get('mode', 'auto'))}
{option('color', 'Color', f.get('mode', 'auto'))}
{option('bw', 'Black & white', f.get('mode', 'auto'))}
</select>
</p>
<p>
<label for="palette">Palette</label>
<select id="palette" name="palette">
{option('adaptive', 'Adaptive (best N colors from the image)', f.get('palette', 'adaptive'))}
{option('paint', 'MS Paint classic (28 colors)', f.get('palette', 'adaptive'))}
{option('win16', 'Windows 16 colors', f.get('palette', 'adaptive'))}
{option('websafe', 'Web-safe 216 (Netscape)', f.get('palette', 'adaptive'))}
</select>
<label for="colors">N =</label>
<input id="colors" name="colors" type="number" min="2" max="256" value="{e(f.get('colors', '16'))}" size="4">
<small>Photoshop-style: 9–16 colors, little or no dither.</small>
</p>
<p>
<label for="dither">Dither</label>
<select id="dither" name="dither">
{option('none', 'None (flat, hardest edges)', f.get('dither', 'none'))}
{option('diffusion', 'Diffusion (Floyd–Steinberg)', f.get('dither', 'none'))}
{option('pattern', 'Pattern (ordered 8×8 Bayer)', f.get('dither', 'none'))}
{option('noise', 'Noise', f.get('dither', 'none'))}
</select>
<label for="dither_strength">strength</label>
<input id="dither_strength" name="dither_strength" type="number" min="0" max="100" value="{e(f.get('dither_strength', '60'))}" size="3"> %
<small>(pattern and noise only)</small>
</p>
<p><label><input type="checkbox" name="sharpen"{checked(f, 'sharpen')}> Sharpen before the palette snap (unsharp mask; crisper edges)</label></p>
<p><label><input type="checkbox" name="contrast"{checked(f, 'contrast')}> Contrast boost (hue-safe)</label></p>
<p><label><input type="checkbox" name="despeckle"{checked(f, 'despeckle')}> Despeckle (3×3 median; removes grain, costs fine detail)</label></p>
<p>
<label for="threshold">Black &amp; white threshold</label>
<input id="threshold" name="threshold" type="number" min="0" max="255" value="{e(f.get('threshold', ''))}" size="4">
<small>blank = automatic (Otsu); 128 = Photoshop's 50%</small>
</p>
<p>
<label for="matte">Matte for transparent pixels</label>
<select id="matte" name="matte">
{option('white', 'White', f.get('matte', 'white'))}
{option('gray', 'Netscape gray', f.get('matte', 'white'))}
{option('black', 'Black', f.get('matte', 'white'))}
</select>
</p>
</fieldset>

<fieldset>
<legend>Size</legend>
<p>
<label for="grid_width">Pixel grid width</label>
<input id="grid_width" name="grid_width" type="number" min="16" max="16384" value="{e(f.get('grid_width', ''))}" size="6" placeholder="native">
<small>Blank keeps every source pixel (recommended). A number downsamples once to a chunky grid.</small>
</p>
<p>
<label for="min_output_width">Minimum output width</label>
<input id="min_output_width" name="min_output_width" type="number" min="0" max="32768" value="{e(f.get('min_output_width', '3200'))}" size="6">
<small>Smaller results are multiplied by a whole number (nearest neighbour, no resampling) until at least this wide. 0 = never.</small>
</p>
<p>
<label for="fmt">Format</label>
<select id="fmt" name="fmt">
{option('png', 'PNG (indexed)', f.get('fmt', 'png'))}
{option('bmp', 'BMP (indexed bitmap)', f.get('fmt', 'png'))}
{option('gif', 'GIF', f.get('fmt', 'png'))}
{option('all', 'PNG + BMP + GIF', f.get('fmt', 'png'))}
</select>
</p>
</fieldset>

<fieldset>
<legend>Output</legend>
<p>
<label for="out_dir">Output folder</label><br>
<input id="out_dir" name="out_dir" type="text" size="60" value="{e(f.get('out_dir', ''))}" placeholder="{e(str(next_run))}">
<small>Blank = the next numbered run folder, shown above.</small>
</p>
</fieldset>

<p><button>Process</button></p>
</form>
"""
    return page("Red Sun", body)


def render_job(job: Job) -> str:
    total, done = len(job.files), len(job.results)
    failed = sum(1 for r in job.results if r.error)
    outputs = job.outputs()
    index = {p: i for i, (_, p) in enumerate(outputs)}
    rows = []
    for r in job.results:
        if r.error:
            rows.append(f"<tr><td>{e(r.source.name)}</td><td colspan=\"3\"></td><td>Failed: {e(r.error)}</td></tr>")
            continue
        links = ", ".join(f'<a href="/jobs/{job.id}/out/{index[p]}">{e(p.name)}</a>' for p in r.outputs)
        scale = f" ({r.factor}×)" if r.factor > 1 else ""
        rows.append(
            f"<tr><td>{e(r.source.name)}</td><td>{links}</td><td>{r.width}×{r.height}{scale}</td>"
            f"<td>{r.colors}</td><td>{'B&amp;W' if r.mode == 'bw' else 'Color'}</td></tr>"
        )
    table = f"""
<table>
<thead><tr><th scope="col">Source</th><th scope="col">Output</th><th scope="col">Size</th><th scope="col">Colors</th><th scope="col">Result</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>""" if rows else ""

    if job.error:
        status = f"<p><strong>Problem:</strong> {e(job.error)}</p>"
    elif job.finished:
        status = (f"<p><strong>Finished.</strong> {done - failed} of {total} images saved to <code>{e(str(job.out_dir))}</code>."
                  + (f" {failed} failed." if failed else "") + "</p>")
    else:
        status = f"<p>Processing {done} of {total}… this page refreshes itself.</p>"
    progress = f'<progress value="{done}" max="{total}"></progress>'

    figures = ""
    if job.finished and not job.error:
        previews = [(r, p) for r, p in outputs if p.suffix == ".png"] or [(r, p) for r, p in outputs if p.suffix != ".bmp"]
        figures = "\n".join(
            f'<figure><a href="/jobs/{job.id}/view/{index[p]}"><img src="/jobs/{job.id}/out/{index[p]}?crop=1" alt="1:1 detail of {e(p.name)}"></a>'
            f'<figcaption>{e(p.name)}: 1:1 centre detail, {r.width}×{r.height}, {r.colors} colors. '
            f'<a href="/jobs/{job.id}/view/{index[p]}">View every pixel</a></figcaption></figure>'
            for r, p in previews
        )
        figures = f"<h2>Results</h2><p>Each detail below is an unscaled 1:1 crop of the saved file.</p>{figures}" if figures else ""
    actions = f"""
<p><a href="/">Process more images</a></p>
<form method="post" action="/jobs/{job.id}/reveal">
<input type="hidden" name="session" value="{SESSION}">
<button>Show output folder</button>
</form>""" if job.finished else ""
    return page("Red Sun — batch", f"{status}{progress}{table}{actions}{figures}", refresh=None if job.finished else 2)


def render_view(job: Job, index: int) -> str:
    result, path = job.outputs()[index]
    body = f"""
<h2>{e(path.name)}</h2>
<p>{result.width}×{result.height}, {result.colors} colors, shown at natural size with nearest-neighbour rendering.
Zoom the browser (⌘+ or Ctrl+) to inspect the pixel boundaries. <a href="/jobs/{job.id}">Back to the batch</a>.</p>
<img src="/jobs/{job.id}/out/{index}" alt="{e(path.name)}" width="{result.width}" height="{result.height}">
"""
    return page(f"Red Sun — {path.name}", body)


# ---------------------------------------------------------------- job runner

def start_job(settings: core.Settings, files: list[Path], out_dir: Path | None, upload_dir: Path | None) -> Job:
    with LOCK:
        if out_dir is None:
            while True:  # numbered run folder; the mkdir is the race guard
                out_dir = core.next_run_dir()
                try:
                    out_dir.mkdir(parents=True, exist_ok=False)
                    break
                except FileExistsError:
                    continue
        job = Job(id=secrets.token_hex(6), settings=settings, files=files, out_dir=out_dir, upload_dir=upload_dir)
        JOBS[job.id] = job

    def work() -> None:
        try:
            core.run_batch(files, out_dir, settings, progress=lambda i, n, r: job.results.append(r))
        except Exception as exc:  # noqa: BLE001
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = True
            if upload_dir:
                shutil.rmtree(upload_dir, ignore_errors=True)

    threading.Thread(target=work, name=f"job-{job.id}", daemon=True).start()
    return job


def crop_png(path: Path) -> bytes:
    """Unscaled centre crop: what the pixels really look like."""
    with Image.open(path) as im:
        im.load()
        w, h = min(CROP[0], im.width), min(CROP[1], im.height)
        left, top = (im.width - w) // 2, (im.height - h) // 2
        buf = io.BytesIO()
        im.crop((left, top, left + w, top + h)).save(buf, "PNG")
        return buf.getvalue()


def reveal(folder: Path) -> None:
    system = platform.system()
    cmd = ["open", str(folder)] if system == "Darwin" else ["explorer", str(folder)] if system == "Windows" else ["xdg-open", str(folder)]
    subprocess.Popen(cmd)  # explicit argv, never a shell string


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = f"RedSun/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- helpers
    def send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def html(self, markup: str, status: int = 200) -> None:
        self.send(status, markup.encode("utf-8"), "text/html; charset=utf-8")

    def text(self, status: int, message: str) -> None:
        self.send(status, message.encode("utf-8"), "text/plain; charset=utf-8")

    def redirect(self, location: str) -> None:
        self.send(303, b"", "text/plain", {"Location": location})

    def form(self) -> tuple[dict[str, str], list[Path], Path | None]:
        """Returns (fields, uploaded files, upload dir or None). Multipart bodies stream to a temp dir."""
        length = int(self.headers.get("Content-Length") or 0)
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            match = re.search(r'boundary="?([^";]+)"?', ctype)
            if not match:
                raise ValueError("multipart boundary missing")
            upload_dir = Path(tempfile.mkdtemp(prefix="red-sun-upload-"))
            try:
                fields, files = parse_multipart(self.rfile, match.group(1).encode(), length, upload_dir)
            except Exception:
                shutil.rmtree(upload_dir, ignore_errors=True)
                raise
            return fields, files, upload_dir
        if length > MAX_TEXT_BYTES:
            raise ValueError("form too large")
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}, [], None

    def authorized(self, fields: dict[str, str]) -> bool:
        if not secrets.compare_digest(fields.get("session", ""), SESSION):
            return False
        site = self.headers.get("Sec-Fetch-Site")
        if site:
            return site in ("same-origin", "none")
        origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
        return origin.startswith(f"http://{self.headers.get('Host', '')}")

    def job(self, job_id: str) -> Job | None:
        with LOCK:
            return JOBS.get(job_id)

    # -- GET
    def do_GET(self) -> None:
        url = urlsplit(self.path)
        path = url.path
        if path == "/":
            return self.html(render_form(defaults()))
        if path == "/health/ready":
            return self.send(200, json.dumps({"status": "ready", "version": __version__}).encode(), "application/json")
        if path == "/favicon.ico":
            return self.static("favicon.ico")
        if path.startswith("/static/"):
            return self.static(path[len("/static/"):])
        if m := re.fullmatch(r"/jobs/([0-9a-f]{12})", path):
            job = self.job(m.group(1))
            return self.html(render_job(job)) if job else self.text(404, "No such batch.")
        if m := re.fullmatch(r"/jobs/([0-9a-f]{12})/(out|view)/(\d+)", path):
            job = self.job(m.group(1))
            index = int(m.group(3))
            if not job or index >= len(job.outputs()):
                return self.text(404, "No such output.")
            if m.group(2) == "view":
                return self.html(render_view(job, index))
            return self.output(job, index, "crop=1" in url.query)
        self.text(404, "Not found.")

    do_HEAD = do_GET

    def static(self, name: str) -> None:
        ctype = STATIC_FILES.get(name)
        if not ctype:
            return self.text(404, "Not found.")
        self.send(200, (STATIC / name).read_bytes(), ctype)

    def output(self, job: Job, index: int, crop: bool) -> None:
        path = job.outputs()[index][1]
        if crop:
            return self.send(200, crop_png(path), "image/png")
        ctype = {".png": "image/png", ".bmp": "image/bmp", ".gif": "image/gif"}[path.suffix]
        self.send(200, path.read_bytes(), ctype, {"Content-Disposition": f'inline; filename="{path.name}"'})

    # -- POST
    def do_POST(self) -> None:
        try:
            fields, uploads, upload_dir = self.form()
        except ValueError as exc:
            return self.text(400, str(exc))
        if not self.authorized(fields):
            if upload_dir:
                shutil.rmtree(upload_dir, ignore_errors=True)
            return self.text(403, "Forbidden: actions must come from the Red Sun page open in this browser.")
        path = urlsplit(self.path).path
        if path == "/jobs":
            return self.create_job(fields, uploads, upload_dir)
        if upload_dir:
            shutil.rmtree(upload_dir, ignore_errors=True)
        if m := re.fullmatch(r"/jobs/([0-9a-f]{12})/reveal", path):
            job = self.job(m.group(1))
            if not job:
                return self.text(404, "No such batch.")
            reveal(job.out_dir)
            return self.redirect(f"/jobs/{job.id}")
        if path == "/quit":
            self.html(page("Red Sun", "<p>Red Sun has quit. You can close this tab.</p>"))
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self.text(404, "Not found.")

    def create_job(self, fields: dict[str, str], uploads: list[Path], upload_dir: Path | None) -> None:
        def fail(message: str) -> None:
            if upload_dir:
                shutil.rmtree(upload_dir, ignore_errors=True)
            self.html(render_form(fields, message), 400)

        try:
            settings = settings_from(fields)
        except ValueError as exc:
            return fail(str(exc))
        files = [p for p in uploads if p.suffix.lower() in core.SUPPORTED and not p.name.startswith(".")]
        typed = fields.get("path", "").strip()
        if typed:
            source = clean_path(typed)
            if not source.exists():
                return fail(f"Nothing exists at {source}")
            files += core.collect_files(source, recursive="recursive" in fields)
        if not files:
            kinds = ", ".join(sorted(core.SUPPORTED))
            return fail(f"Choose images, a folder, or type a path first (supported: {kinds}).")
        out_dir = clean_path(fields["out_dir"]) if fields.get("out_dir", "").strip() else None
        job = start_job(settings, files, out_dir, upload_dir)
        self.redirect(f"/jobs/{job.id}")

    def log_message(self, fmt: str, *args) -> None:  # keep launcher logs readable, one line per request
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} {fmt % args}\n")


def make_server(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Bind atomically; the OS picks a free port when port is 0."""
    ThreadingHTTPServer.allow_reuse_address = False
    ThreadingHTTPServer.daemon_threads = True
    return ThreadingHTTPServer((host, port), Handler)
