"""Red Sun local GUI: plain HTML forms, zero JavaScript, bound to 127.0.0.1 only.

Every page is rendered here from the same core the CLI uses. State-changing
requests need the per-launch session token (a hidden form field) and a
same-origin check, so a malicious website cannot drive the app.
"""

from __future__ import annotations

import html
import io
import json
import os
import platform
import re
import secrets
import subprocess
import sys
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
MAX_FORM_BYTES = 65536
PREVIEW_WIDTH = 480

e = html.escape


@dataclass
class Job:
    id: str
    settings: core.Settings
    files: list[Path]
    out_dir: Path
    results: list[core.Result] = field(default_factory=list)
    finished: bool = False
    error: str | None = None

    def outputs(self) -> list[tuple[core.Result, Path]]:
        return [(r, p) for r in self.results for p in r.outputs]


JOBS: dict[str, Job] = {}
LOCK = threading.Lock()


# ---------------------------------------------------------------- form model

def defaults() -> dict[str, str]:
    s = core.Settings()
    return {
        "path": "", "out_dir": "", "mode": s.mode, "palette": s.palette, "colors": str(s.colors),
        "despeckle": "on", "contrast": "on", "threshold": "", "grid_width": str(s.grid_width),
        "output_width": str(s.output_width), "fmt": s.fmt,
    }


def settings_from(f: dict[str, str]) -> core.Settings:
    def num(name: str, default):
        value = f.get(name, "").strip()
        return int(value) if value else default

    s = core.Settings(
        mode=f.get("mode", "auto"), palette=f.get("palette", "paint"), colors=num("colors", 16),
        dither="dither" in f, despeckle="despeckle" in f, contrast="contrast" in f,
        threshold=num("threshold", None), grid_width=num("grid_width", 640),
        output_width=num("output_width", 3200), fmt=f.get("fmt", "png"),
    )
    s.validate()
    return s


def clean_path(text: str) -> Path:
    return Path(text.strip().strip('"\'')).expanduser()


# ---------------------------------------------------------------- rendering

def page(title: str, body: str, refresh: int | None = None) -> str:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    data_dir = os.environ.get("GOOSNAV_DATA_DIR", "")
    where = f"<p>Application data: <code>{e(data_dir)}</code></p>" if data_dir else ""
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
<p>Turn any photo into a sharp, color-indexed MS Paint style bitmap. Big enough to print, clean enough to post.</p>
</header>
<main>
{body}
</main>
<hr>
<footer>
<p>Red Sun {e(__version__)}. Outputs are saved as indexed PNG/BMP next to your images unless you choose another folder. Original files are never modified.</p>
{where}
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
    try:
        factor = settings_from(f).factor()
        factor_note = f"Currently {factor}× nearest-neighbour."
    except ValueError:
        factor_note = ""
    body = f"""
{alert}
<form method="post" action="/jobs">
<input type="hidden" name="session" value="{SESSION}">
<button hidden tabindex="-1">Process</button>
<!-- first submit button = what Enter does; keeps Enter from triggering Browse -->

<fieldset>
<legend>Source</legend>
<p>
<label for="path">Image file or folder</label><br>
<input id="path" name="path" type="text" size="60" value="{e(f.get('path', ''))}" placeholder="/Users/you/Pictures/scans">
<button formaction="/browse/file">Browse for a file…</button>
<button formaction="/browse/folder">Browse for a folder…</button>
</p>
<p><label><input type="checkbox" name="recursive"{checked(f, 'recursive')}> Include subfolders</label></p>
<p>
<label for="out_dir">Output folder (optional)</label><br>
<input id="out_dir" name="out_dir" type="text" size="60" value="{e(f.get('out_dir', ''))}" placeholder="Leave blank for a red_sun folder next to the source">
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
{option('paint', 'MS Paint classic (28 colors)', f.get('palette', 'paint'))}
{option('win16', 'Windows 16 colors', f.get('palette', 'paint'))}
{option('adaptive', 'Adaptive (best N colors from the image)', f.get('palette', 'paint'))}
</select>
<label for="colors">N =</label>
<input id="colors" name="colors" type="number" min="2" max="256" value="{e(f.get('colors', '16'))}" size="4">
</p>
<p><label><input type="checkbox" name="dither"{checked(f, 'dither')}> Dither (speckled shading; off keeps flat, hard regions)</label></p>
<p><label><input type="checkbox" name="despeckle"{checked(f, 'despeckle')}> Despeckle (remove grain and stray pixels; keeps edges hard)</label></p>
<p><label><input type="checkbox" name="contrast"{checked(f, 'contrast')}> Contrast boost (punchier blacks and whites; hue-safe)</label></p>
<p>
<label for="threshold">Black &amp; white threshold (0–255, blank = automatic)</label>
<input id="threshold" name="threshold" type="number" min="0" max="255" value="{e(f.get('threshold', ''))}" size="4">
</p>
</fieldset>

<fieldset>
<legend>Size</legend>
<p>
<label for="grid_width">Pixel grid width</label>
<input id="grid_width" name="grid_width" type="number" min="16" max="4096" value="{e(f.get('grid_width', '640'))}" size="6">
<small>How many “Paint pixels” across. Lower = chunkier.</small>
</p>
<p>
<label for="output_width">Output width</label>
<input id="output_width" name="output_width" type="number" min="16" max="16384" value="{e(f.get('output_width', '3200'))}" size="6">
<small>Rounded to a whole multiple of the grid so every block stays crisp. {factor_note}</small>
</p>
<p>
<label for="fmt">Format</label>
<select id="fmt" name="fmt">
{option('png', 'PNG (indexed)', f.get('fmt', 'png'))}
{option('bmp', 'BMP (indexed bitmap)', f.get('fmt', 'png'))}
{option('both', 'Both', f.get('fmt', 'png'))}
</select>
</p>
</fieldset>

<p><button>Process</button></p>
</form>
"""
    return page("Red Sun", body)


def render_job(job: Job) -> str:
    total, done = len(job.files), len(job.results)
    failed = sum(1 for r in job.results if r.error)
    rows = []
    outputs = job.outputs()
    index = {p: i for i, (_, p) in enumerate(outputs)}
    for r in job.results:
        if r.error:
            rows.append(f"<tr><td>{e(r.source.name)}</td><td colspan=\"3\"></td><td>Failed: {e(r.error)}</td></tr>")
            continue
        links = ", ".join(f'<a href="/jobs/{job.id}/out/{index[p]}">{e(p.name)}</a>' for p in r.outputs)
        rows.append(
            f"<tr><td>{e(r.source.name)}</td><td>{links}</td><td>{r.width}×{r.height}</td>"
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
        status = f"<p><strong>Finished.</strong> {done - failed} of {total} images saved to <code>{e(str(job.out_dir))}</code>." + (f" {failed} failed." if failed else "") + "</p>"
    else:
        status = f"<p>Processing {done} of {total}… this page refreshes itself.</p>"
    progress = f'<progress value="{done}" max="{total}"></progress>'

    figures = ""
    if job.finished and not job.error:
        previews = [(r, p) for r, p in outputs if p.suffix == ".png"]
        figures = "\n".join(
            f'<figure><a href="/jobs/{job.id}/out/{index[p]}"><img src="/jobs/{job.id}/out/{index[p]}?preview=1" alt="{e(p.name)}" width="{PREVIEW_WIDTH}"></a>'
            f"<figcaption>{e(p.name)} — {r.width}×{r.height}, {r.colors} colors</figcaption></figure>"
            for r, p in previews
        )
        figures = f"<h2>Results</h2>{figures}" if figures else ""
    actions = f"""
<p>
<a href="/">Process more images</a>
</p>
<form method="post" action="/jobs/{job.id}/reveal">
<input type="hidden" name="session" value="{SESSION}">
<button>Show output folder</button>
</form>""" if job.finished else ""
    body = f"{status}{progress}{table}{actions}{figures}"
    return page("Red Sun — batch", body, refresh=None if job.finished else 2)


# ---------------------------------------------------------------- job runner

def start_job(settings: core.Settings, files: list[Path], out_dir: Path) -> Job:
    job = Job(id=secrets.token_hex(6), settings=settings, files=files, out_dir=out_dir)
    with LOCK:
        JOBS[job.id] = job

    def work() -> None:
        try:
            core.run_batch(files, out_dir, settings, progress=lambda i, n, r: job.results.append(r))
        except Exception as exc:  # noqa: BLE001
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = True

    threading.Thread(target=work, name=f"job-{job.id}", daemon=True).start()
    return job


def preview_png(path: Path, factor: int) -> bytes:
    with Image.open(path) as im:
        im.load()
        small = im.resize((max(1, im.width // factor), max(1, im.height // factor)), Image.Resampling.NEAREST)
        if small.width > PREVIEW_WIDTH:
            small = small.resize((PREVIEW_WIDTH, round(small.height * PREVIEW_WIDTH / small.width)), Image.Resampling.NEAREST)
        buf = io.BytesIO()
        small.save(buf, "PNG")
        return buf.getvalue()


def reveal(folder: Path) -> None:
    system = platform.system()
    cmd = ["open", str(folder)] if system == "Darwin" else ["explorer", str(folder)] if system == "Windows" else ["xdg-open", str(folder)]
    subprocess.Popen(cmd)  # explicit argv, never a shell string


def native_pick(kind: str, initial: str) -> tuple[str | None, str | None]:
    """Returns (path, problem). A cancelled dialog returns ("", None)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "red_sun.pick", kind, initial], capture_output=True, text=True, timeout=600,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        )
    except subprocess.TimeoutExpired:
        return None, "The file dialog timed out."
    if proc.returncode != 0:
        return None, "The native file dialog is not available here. Type or paste the path instead."
    return proc.stdout.strip(), None


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

    def form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_FORM_BYTES:
            raise ValueError("form too large")
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}

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
        if m := re.fullmatch(r"/jobs/([0-9a-f]{12})/out/(\d+)", path):
            return self.output(m.group(1), int(m.group(2)), "preview=1" in url.query)
        self.text(404, "Not found.")

    do_HEAD = do_GET

    def static(self, name: str) -> None:
        ctype = STATIC_FILES.get(name)
        if not ctype:
            return self.text(404, "Not found.")
        self.send(200, (STATIC / name).read_bytes(), ctype)

    def output(self, job_id: str, index: int, preview: bool) -> None:
        job = self.job(job_id)
        outputs = job.outputs() if job else []
        if index >= len(outputs):
            return self.text(404, "No such output.")
        path = outputs[index][1]
        if preview:
            return self.send(200, preview_png(path, job.settings.factor()), "image/png")
        ctype = "image/png" if path.suffix == ".png" else "image/bmp"
        self.send(200, path.read_bytes(), ctype, {"Content-Disposition": f'inline; filename="{path.name}"'})

    # -- POST
    def do_POST(self) -> None:
        try:
            fields = self.form()
        except ValueError as exc:
            return self.text(400, str(exc))
        if not self.authorized(fields):
            return self.text(403, "Forbidden: actions must come from the Red Sun page open in this browser.")
        path = urlsplit(self.path).path
        if path == "/jobs":
            return self.create_job(fields)
        if path in ("/browse/file", "/browse/folder"):
            return self.browse(fields, path.rsplit("/", 1)[1])
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

    def browse(self, fields: dict[str, str], kind: str) -> None:
        current = fields.get("path", "").strip()
        initial = str(clean_path(current).parent if current and clean_path(current).is_file() else clean_path(current)) if current else ""
        chosen, problem = native_pick(kind, initial)
        if chosen:
            fields["path"] = chosen
        self.html(render_form(fields, problem))

    def create_job(self, fields: dict[str, str]) -> None:
        try:
            settings = settings_from(fields)
        except ValueError as exc:
            return self.html(render_form(fields, str(exc)), 400)
        if not fields.get("path", "").strip():
            return self.html(render_form(fields, "Choose an image file or a folder first."), 400)
        source = clean_path(fields["path"])
        if not source.exists():
            return self.html(render_form(fields, f"Nothing exists at {source}"), 400)
        files = core.collect_files(source, recursive="recursive" in fields)
        if not files:
            kinds = ", ".join(sorted(core.SUPPORTED))
            return self.html(render_form(fields, f"No supported images found ({kinds})."), 400)
        out_dir = clean_path(fields["out_dir"]) if fields.get("out_dir", "").strip() else core.default_out_dir(source)
        job = start_job(settings, files, out_dir)
        self.redirect(f"/jobs/{job.id}")

    def log_message(self, fmt: str, *args) -> None:  # keep launcher logs readable, one line per request
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} {fmt % args}\n")


def make_server(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Bind atomically; the OS picks a free port when port is 0."""
    ThreadingHTTPServer.allow_reuse_address = False
    ThreadingHTTPServer.daemon_threads = True
    return ThreadingHTTPServer((host, port), Handler)
