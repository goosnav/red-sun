"""Red Sun local GUI: plain HTML forms, zero JavaScript, bound to 127.0.0.1 only.

Every page is rendered here from the same core the CLI uses. State-changing
requests need the per-launch session token (a hidden form field) and a
same-origin check, so a malicious website cannot drive the app. Folders are
chosen with the operating system's own Choose Folder dialog (macOS panel via
osascript, Windows via PowerShell, Linux via zenity/kdialog), so a real path
comes back and results can be written beside the pictures. Settings persist to
a small JSON file so the app reopens the way it was left.
"""

from __future__ import annotations

import html
import io
import json
import os
import platform
import re
import secrets
import shutil
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
CROP = (480, 320)

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


# ---------------------------------------------------------------- settings persistence

def config_dir() -> Path:
    if env := os.environ.get("GOOSNAV_CONFIG_DIR"):
        return Path(env)
    home, system = Path.home(), platform.system()
    if system == "Darwin":
        return home / "Library" / "Application Support" / "Red Sun" / "config"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", home)) / "Red Sun"
    return Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "red-sun"


def defaults() -> dict[str, str]:
    s = core.Settings()
    return {
        "path": "", "out_dir": "", "mode": s.mode, "palette": s.palette, "colors": str(s.colors),
        "dither": s.dither, "dither_strength": str(s.dither_strength),
        "threshold": "", "matte": s.matte, "grid_width": "", "min_output_width": str(s.min_output_width), "fmt": s.fmt,
        "bw_dither": s.bw_dither, "bw_dither_strength": str(s.bw_dither_strength),
    }


CHECKBOXES = {"recursive", "sharpen", "despeckle", "contrast", "bw_sharpen", "bw_despeckle", "bw_contrast"}
FORM_KEYS = set(defaults()) | CHECKBOXES


def load_fields() -> dict[str, str]:
    """Last saved form, or the defaults."""
    try:
        saved = json.loads((config_dir() / "settings.json").read_text(encoding="utf-8"))
        return {k: str(v) for k, v in saved.items() if k in FORM_KEYS}
    except (OSError, ValueError):
        return defaults()


def save_fields(fields: dict[str, str]) -> None:
    keep = {k: v for k, v in fields.items() if k in FORM_KEYS}
    try:
        directory = config_dir()
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / "settings.json.tmp"
        tmp.write_text(json.dumps(keep, indent=2), encoding="utf-8")
        os.replace(tmp, directory / "settings.json")
    except OSError as exc:  # settings are a convenience; never block processing on them
        sys.stderr.write(f"settings not saved: {exc}\n")


def settings_from(f: dict[str, str]) -> core.Settings:
    def num(name: str, default):
        value = f.get(name, "").strip()
        return int(value) if value else default

    s = core.Settings(
        mode=f.get("mode", "auto"), palette=f.get("palette", "dominant"), colors=num("colors", 16),
        dither=f.get("dither", "none"), dither_strength=num("dither_strength", 60),
        sharpen="sharpen" in f, despeckle="despeckle" in f, contrast="contrast" in f,
        threshold=num("threshold", None), matte=f.get("matte", "white"), grid_width=num("grid_width", None),
        min_output_width=num("min_output_width", 3200), fmt=f.get("fmt", "png"),
        bw_dither=f.get("bw_dither", "none"), bw_dither_strength=num("bw_dither_strength", 60),
        bw_sharpen="bw_sharpen" in f, bw_despeckle="bw_despeckle" in f, bw_contrast="bw_contrast" in f,
    )
    s.validate()
    return s


def clean_path(text: str) -> Path:
    return Path(text.strip().strip('"\'')).expanduser()


# ---------------------------------------------------------------- the system's own folder dialog

DIALOG_TIMEOUT = 600  # seconds a dialog may stay open before it counts as cancelled
IMAGE_EXTENSIONS = "*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.bmp;*.webp;*.gif"


def start_dir(fields: dict[str, str]) -> Path:
    """Where the dialog opens: the last folder, else Pictures, else home."""
    for key in ("path", "out_dir"):
        if fields.get(key, "").strip():
            candidate = core.source_folder(clean_path(fields[key]))
            if candidate.is_dir():
                return candidate
    pictures = Path.home() / "Pictures"
    return pictures if pictures.is_dir() else Path.home()


def dialog_command(kind: str, start: Path) -> list[str]:
    """argv for the OS dialog. kind: 'source' (folder of pictures), 'file' (one image), 'out' (output folder)."""
    prompt = {"source": "Choose the folder with your pictures", "file": "Choose one image", "out": "Choose where to save the results"}[kind]
    system = platform.system()
    if system == "Darwin":
        quoted = str(start).replace("\\", "\\\\").replace('"', '\\"')
        chooser = 'choose file of type {"public.image"}' if kind == "file" else "choose folder"
        script = (
            "activate\n"  # bring the panel in front of the browser
            f'set chosen to {chooser} with prompt "{prompt}" default location (POSIX file "{quoted}" as alias)\n'
            "return POSIX path of chosen"
        )
        return ["osascript", "-e", script]
    if system == "Windows":
        quoted = str(start).replace("'", "''")
        if kind == "file":
            dialog = (f"$d = New-Object System.Windows.Forms.OpenFileDialog; $d.Title = '{prompt}'; "
                      f"$d.Filter = 'Images|{IMAGE_EXTENSIONS}|All files|*.*'; $d.InitialDirectory = '{quoted}'; "
                      "if ($d.ShowDialog($owner) -eq 'OK') { $d.FileName } else { exit 1 }")
        else:
            dialog = (f"$d = New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description = '{prompt}'; "
                      f"$d.SelectedPath = '{quoted}'; $d.ShowNewFolderButton = $true; "
                      "if ($d.ShowDialog($owner) -eq 'OK') { $d.SelectedPath } else { exit 1 }")
        script = ("Add-Type -AssemblyName System.Windows.Forms; "
                  "$owner = New-Object System.Windows.Forms.Form; $owner.TopMost = $true; " + dialog)
        return ["powershell", "-NoProfile", "-STA", "-Command", script]
    if shutil.which("zenity"):
        base = ["zenity", "--file-selection", f"--title={prompt}", f"--filename={start}/"]
        return base if kind == "file" else base + ["--directory"]
    if kind == "file":
        return ["kdialog", "--getopenfilename", str(start), "image/*", "--title", prompt]
    return ["kdialog", "--getexistingdirectory", str(start), "--title", prompt]


def native_pick(kind: str, start: Path) -> tuple[str | None, str | None]:
    """Show the OS dialog. Returns (path, None); ("", None) when cancelled; (None, problem) when unavailable."""
    if not start.is_dir():
        start = Path.home()
    try:
        proc = subprocess.run(dialog_command(kind, start), capture_output=True, text=True, timeout=DIALOG_TIMEOUT)
    except FileNotFoundError:
        return None, "No folder dialog is available on this system (on Linux install zenity or kdialog)."
    except subprocess.TimeoutExpired:
        return "", None
    if proc.returncode != 0:  # cancelled ("User canceled." on macOS, exit 1 elsewhere)
        return "", None
    chosen = proc.stdout.strip()
    if len(chosen) > 1:
        chosen = chosen.rstrip("/\\")
    return chosen, None


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
<p>Red Sun {e(__version__)}. Results go into a new numbered <code>red-sun-run-NNN</code> folder right next to your pictures unless you choose another folder. Original files are never modified. Your settings are remembered between sessions.</p>
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


def describe_source(text: str) -> str:
    if not text.strip():
        return "Nothing selected yet."
    path = clean_path(text)
    if path.is_file():
        return f"One image: {e(path.name)}. Results go to a new folder next to it."
    if path.is_dir():
        count = len(core.collect_files(path))
        return f"{count} image{'s' if count != 1 else ''} in this folder (subfolders not counted). Results go to <code>{e(str(core.next_run_dir(path)))}</code>."
    return "This path does not exist."


def render_form(f: dict[str, str], problem: str | None = None) -> str:
    alert = f"<p><strong>Problem:</strong> {e(problem)}</p>" if problem else ""
    body = f"""
{alert}
<form method="post" action="/jobs">
<input type="hidden" name="session" value="{SESSION}">
<button hidden tabindex="-1">Process</button>
<!-- first submit button = what Enter does; keeps Enter from opening a dialog -->

<fieldset>
<legend>Source</legend>
<p>
<label for="path">Folder (or one image) to process</label><br>
<input id="path" name="path" type="text" size="60" value="{e(f.get('path', ''))}" placeholder="Click Choose folder…">
<button formaction="/choose" name="for" value="source">Choose folder…</button>
<button formaction="/choose" name="for" value="file">Choose one image…</button>
</p>
<p>{describe_source(f.get('path', ''))}</p>
<p><label><input type="checkbox" name="recursive"{checked(f, 'recursive')}> Include subfolders (earlier run folders are skipped)</label></p>
</fieldset>

<fieldset>
<legend>Look</legend>
<p>
<label for="mode">Mode</label>
<select id="mode" name="mode">
{option('auto', 'Auto: decide per image (grayscale sources get the B&W look)', f.get('mode', 'auto'))}
{option('color', 'Treat every image as color', f.get('mode', 'auto'))}
{option('bw', 'Treat every image as black & white', f.get('mode', 'auto'))}
</select>
</p>
<p><small>A folder can mix colour and black &amp; white images: each gets its own look below. Settings for a kind that is not present are simply unused.</small></p>
</fieldset>

<fieldset>
<legend>Color images</legend>
<p>
<label for="palette">Palette</label>
<select id="palette" name="palette">
{option('dominant', 'Flat buckets: the image’s own colors, up to N (comics, art)', f.get('palette', 'dominant'))}
{option('adaptive', 'Adaptive median cut, exactly N (photos)', f.get('palette', 'dominant'))}
{option('paint', 'MS Paint classic (28 colors)', f.get('palette', 'dominant'))}
{option('win16', 'Windows 16 colors', f.get('palette', 'dominant'))}
{option('websafe', 'Web-safe 216 (Netscape)', f.get('palette', 'dominant'))}
</select>
<label for="colors">N =</label>
<input id="colors" name="colors" type="number" min="2" max="256" value="{e(f.get('colors', '16'))}" size="4">
<small>Every pixel is snapped to one bucket; edge blends never get a bucket of their own.</small>
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
<p><label><input type="checkbox" name="sharpen"{checked(f, 'sharpen')}> Sharpen before the snap (photos only; adds halos on flat art)</label></p>
<p><label><input type="checkbox" name="contrast"{checked(f, 'contrast')}> Contrast boost (hue-safe)</label></p>
<p><label><input type="checkbox" name="despeckle"{checked(f, 'despeckle')}> Despeckle (3×3 median; removes grain, costs fine detail)</label></p>
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
<legend>Black &amp; white images</legend>
<p>
<label for="threshold">Threshold</label>
<input id="threshold" name="threshold" type="number" min="0" max="255" value="{e(f.get('threshold', ''))}" size="4">
<small>0–255; blank = automatic (Otsu); 128 = Photoshop's 50%</small>
</p>
<p>
<label for="bw_dither">Dither</label>
<select id="bw_dither" name="bw_dither">
{option('none', 'None (pure black or white)', f.get('bw_dither', 'none'))}
{option('diffusion', 'Diffusion (Floyd–Steinberg)', f.get('bw_dither', 'none'))}
{option('pattern', 'Pattern (ordered 8×8 halftone)', f.get('bw_dither', 'none'))}
{option('noise', 'Noise', f.get('bw_dither', 'none'))}
</select>
<label for="bw_dither_strength">strength</label>
<input id="bw_dither_strength" name="bw_dither_strength" type="number" min="0" max="100" value="{e(f.get('bw_dither_strength', '60'))}" size="3"> %
<small>(pattern and noise only; 100 = full halftone)</small>
</p>
<p><label><input type="checkbox" name="bw_sharpen"{checked(f, 'bw_sharpen')}> Sharpen before the threshold</label></p>
<p><label><input type="checkbox" name="bw_contrast"{checked(f, 'bw_contrast')}> Contrast stretch (autocontrast; helps faded scans)</label></p>
<p><label><input type="checkbox" name="bw_despeckle"{checked(f, 'bw_despeckle')}> Despeckle (3×3 median)</label></p>
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
<label for="out_dir">Output folder (optional)</label><br>
<input id="out_dir" name="out_dir" type="text" size="60" value="{e(f.get('out_dir', ''))}" placeholder="a new red-sun-run-NNN folder next to your pictures">
<button formaction="/choose" name="for" value="out">Choose…</button>
</p>
<p><small>Leave blank to get a new numbered folder inside the folder you are processing.</small></p>
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
<form method="post" action="/jobs/{job.id}/reveal">
<input type="hidden" name="session" value="{SESSION}">
<button>Show output folder</button>
</form>
<p><a href="/">Process more images</a></p>""" if job.finished else ""
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

def start_job(settings: core.Settings, files: list[Path], out_dir: Path) -> Job:
    with LOCK:
        job = Job(id=secrets.token_hex(6), settings=settings, files=files, out_dir=out_dir)
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


def create_run_dir(source: Path) -> Path:
    """A fresh red-sun-run-NNN next to the source; the exclusive mkdir is the race guard."""
    root = core.source_folder(source)
    with LOCK:
        while True:
            candidate = core.next_run_dir(root)
            try:
                candidate.mkdir(parents=False, exist_ok=False)
                return candidate
            except FileExistsError:
                continue


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
            return self.html(render_form(load_fields()))
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
            fields = self.form()
        except ValueError as exc:
            return self.text(400, str(exc))
        if not self.authorized(fields):
            return self.text(403, "Forbidden: actions must come from the Red Sun page open in this browser.")
        path = urlsplit(self.path).path
        if path == "/choose":  # remember the form as it is, show the system dialog, remember the answer
            save_fields(fields)
            kind = fields.get("for") if fields.get("for") in ("source", "file", "out") else "source"
            key = "out_dir" if kind == "out" else "path"
            chosen, problem = native_pick(kind, start_dir({key: fields.get(key, "")} if fields.get(key, "").strip() else fields))
            if problem:
                return self.html(render_form(fields, problem))
            if chosen:
                fields[key] = chosen
                save_fields(fields)
            return self.redirect("/")
        if path == "/jobs":
            return self.create_job(fields)
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

    def create_job(self, fields: dict[str, str]) -> None:
        save_fields(fields)
        try:
            settings = settings_from(fields)
        except ValueError as exc:
            return self.html(render_form(fields, str(exc)), 400)
        if not fields.get("path", "").strip():
            return self.html(render_form(fields, "Click Choose folder… and pick the folder with your pictures first."), 400)
        source = clean_path(fields["path"])
        if not source.exists():
            return self.html(render_form(fields, f"Nothing exists at {source}. Click Choose folder… to pick again."), 400)
        files = core.collect_files(source, recursive="recursive" in fields)
        if not files:
            kinds = ", ".join(sorted(core.SUPPORTED))
            return self.html(render_form(fields, f"No supported images found there (supported: {kinds})."), 400)
        try:
            if fields.get("out_dir", "").strip():
                out_dir = clean_path(fields["out_dir"])
                out_dir.mkdir(parents=True, exist_ok=True)
            else:
                out_dir = create_run_dir(source)
        except OSError as exc:
            return self.html(render_form(fields, f"Cannot create the output folder ({exc}). Choose an output folder you can write to."), 400)
        job = start_job(settings, files, out_dir)
        self.redirect(f"/jobs/{job.id}")

    def log_message(self, fmt: str, *args) -> None:  # keep launcher logs readable, one line per request
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} {fmt % args}\n")


def make_server(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Bind atomically; the OS picks a free port when port is 0."""
    ThreadingHTTPServer.allow_reuse_address = False
    ThreadingHTTPServer.daemon_threads = True
    return ThreadingHTTPServer((host, port), Handler)
