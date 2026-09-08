# Red Sun tutorial

## 1. Start it

**App ZIP:** extract the whole ZIP, then double-click the launcher for your system (`Open Red Sun — macOS.app`, `… Windows x64.exe`, and so on). A setup page opens in your browser. The first time it downloads a private Python 3.12 runtime and Pillow (about 40 MB, needs network once). When the bar finishes, the page becomes Red Sun.

**Source checkout:**

```bash
./run.sh
```

or `.\run.ps1` on Windows. Uses `uv` if you have it (recommended; exact locked versions), otherwise makes a `.venv` with Python 3.10+ and installs Pillow. Your browser opens at a local address like `http://127.0.0.1:54321`.

## 2. Pick a folder (or one image)

1. Under **Source**, click **Choose folder…**. Your system's standard folder dialog opens (the macOS Choose Folder panel, the Windows folder dialog). It starts in the last folder you used, or Pictures.
2. Select the folder with your pictures and click **Choose**. To process a single picture instead, click **Choose one image…** and pick it.
3. You are back on the form with the folder filled in and a line telling you how many images it holds and exactly where the results will go.

Tick **Include subfolders** to process the whole tree. Earlier `red-sun-run-…` folders are skipped automatically, so re-running never re-processes old results.

## 3. Process

Leave the defaults (or change the look, see below) and click **Process**. The page refreshes itself every two seconds with a progress bar and a row per image. Files that cannot be read are listed as *Failed* with the reason; the rest still finish.

When it says **Finished**, the table lists each output, its size and colour count. Below that, each result shows an **unscaled 1:1 crop** of the saved file, and a **View every pixel** link that displays the whole file at natural size with nearest-neighbour rendering so browser zoom (⌘+ / Ctrl+) shows the rigid pixel boundaries. **Show output folder** opens the run folder in Finder / Explorer.

## 4. Where the results go

Right next to your pictures: `red-sun-run-001` inside the folder you processed, then `-002`, and so on, each with a `settings.json` recording exactly what produced it. For a single image the run folder is created beside that image. To send results somewhere else, click **Choose…** next to **Output folder** and pick a folder; it is used as-is.

Everything you set on the form, including the last folder, is remembered the next time you open Red Sun.

## 5. Getting the look you want

Red Sun's defaults follow the Photoshop recipe that keeps edges hard: native resolution, **flat buckets** (the image's own colours, up to 16, edge blends excluded, black and white forced in), no dither, no sharpening. Every pixel becomes exactly one bucket colour. The results table tells you how many buckets a file used.

- **Fewer, bolder colours:** lower *N* (the most common colours win), or *Windows 16 colors*.
- **Photos:** *Adaptive* gives exactly N colours by median cut; *Flat buckets* posterizes a photo into its dominant tones.
- **Authentic Paint / Netscape:** *MS Paint classic (28)* or *Web-safe 216*.
- **Classic shading:** *Dither* = *Diffusion* (Floyd–Steinberg grain), *Pattern* (ordered halftone) or *Noise*. Raise *strength* to 100 % for the full-range pattern in black & white; 40–60 % is subtle in colour.
- **Scans, line art, comics:** *Mode* = *Black & white*. Blank threshold = automatic (Otsu); 128 = Photoshop's 50 %. Higher = more black.
- **Grainy or JPEG-noisy photos:** tick *Despeckle*. It removes grain before the snap at the cost of the very finest detail.
- **A logo with transparency:** transparent areas are flattened onto the *Matte* colour (white, Netscape gray or black).
- **Chunky retro pixels on purpose:** set *Pixel grid width* (320, 480, 640…). This is the one place Red Sun resamples, and it does it once, before the snap.
- **Poster:** set *Minimum output width* to 6400 or more. Sources narrower than that are multiplied by a whole number; a 4000 px photo becomes 8000 px at 2×, still an exact indexed file. At 300 dpi, 6400 px is about 21 in / 54 cm wide.

## 6. Command line

Everything the page does, with the same defaults:

```bash
PYTHONPATH=app/src uv run --project app python -m red_sun ./comics --mode bw --threshold 128 --min-output-width 4000
PYTHONPATH=app/src uv run --project app python -m red_sun photo.jpg --colors 9 --dither diffusion --format all
```

`python -m red_sun` with no input opens the GUI. `--out DIR` overrides the numbered run folder.

## 7. Quit

Click **Quit Red Sun** in the page footer (or Ctrl+C in the terminal for `run.sh`). Closing the tab does not stop the server; launching again just reopens the running copy.

## Troubleshooting

| Symptom | What to do |
|---|---|
| "It still looks soft when I zoom in" | Your viewer is smoothing. macOS Preview and bare-image browser tabs interpolate on zoom. Open **View every pixel** in Red Sun, or the file in Photoshop / GIMP, or check the colour count in the results table: an exact 16-colour file cannot contain a gradient. |
| macOS: "cannot be opened because the developer cannot be verified" | Right-click the `.app` → **Open** → **Open**. The launcher is not yet code-signed. |
| Windows: SmartScreen "Windows protected your PC" | **More info** → **Run anyway**. |
| Linux: double-click does nothing | File properties → Permissions → *Allow executing file as program*, then try again. |
| Setup page shows an error code | `BOOT-NETWORK`: first launch needs internet once; click Retry. `LAUNCH-ROOT`: the launcher was moved away from the `app` folder; extract the ZIP again. Other codes name the exact problem; the log path is on the page. |
| The folder dialog does not appear | It may be behind the browser window; check the Dock / taskbar. On Linux, install `zenity` (or `kdialog`). The page waits up to 10 minutes, then treats it as cancelled. |
| macOS asks whether Red Sun may access Desktop / Documents / Downloads | Allow it once; that is the normal system prompt for any app reading those folders. |
| "Cannot create the output folder" | The pictures live somewhere read-only (a camera card, a locked share). Choose an **Output folder** you can write to. |
| "Click Choose folder… and pick the folder with your pictures first" | Nothing was selected; hidden files (leading dot) and non-image files are skipped. |
| Output looks noisy | Turn *Dither* to *None*, tick *Despeckle*, or use fewer colours. |
| A small colour detail vanished | It covered less than 0.2% of the image, so it merged into the nearest bucket. Use *Adaptive* for that file, or raise *N* is not enough: the threshold is fixed. |
| Colours look wrong on a logo | Turn off *Contrast boost*, or use *Adaptive*. |
| Page never loads at `127.0.0.1` | Another program may be blocking loopback; check the terminal (`run.sh`) or the launcher log for the actual port. |

Application data for the ZIP app lives in `~/Library/Application Support/Red Sun` (macOS), `%LOCALAPPDATA%\Red Sun` (Windows), `~/.local/share/Red Sun` (Linux). Deleting that folder resets the runtime; the next launch downloads it again. Your outputs are separate, next to your pictures. Settings live in the app folder's `config/settings.json` (or `~/.config/red-sun` / `%APPDATA%\Red Sun` when run from source).
