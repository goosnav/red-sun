# Red Sun tutorial

## 1. Start it

**App ZIP:** extract the whole ZIP, then double-click the launcher for your system (`Open Red Sun — macOS.app`, `… Windows x64.exe`, and so on). A setup page opens in your browser. The first time it downloads a private Python 3.12 runtime and Pillow (about 40 MB, needs network once). When the bar finishes, the page becomes Red Sun.

**Source checkout:**

```bash
./run.sh
```

or `.\run.ps1` on Windows. Uses `uv` if you have it (recommended; exact locked versions), otherwise makes a `.venv` with Python 3.10+ and installs Pillow. Your browser opens at a local address like `http://127.0.0.1:54321`.

## 2. Convert one image

1. Click **Browse for a file…** and pick a photo (PNG, JPG, TIFF, BMP, WebP, GIF). Or paste a path into the box; quotes are fine.
2. Leave everything at its defaults.
3. Click **Process**.

You land on the batch page. When it says **Finished**, the table lists the output, its size (3200 px wide by default) and how many colours it uses. Scroll down for a preview. Click **Show output folder** to open the `red_sun` folder that was created next to your photo.

## 3. Convert a whole folder

1. Click **Browse for a folder…** (or paste a folder path).
2. Tick **Include subfolders** if you want the whole tree.
3. Click **Process**.

The page refreshes itself every two seconds with a progress bar and a row per image. Files that cannot be read are listed as *Failed* with the reason; the rest still finish. Outputs go to `<that folder>/red_sun/`. Set **Output folder** to send them somewhere else.

## 4. Getting the look you want

- **Chunkier pixels:** lower *Pixel grid width* (320 or 400). Finer: raise it (800, 1024).
- **Poster print:** set *Output width* to 6400 or more. The file is still small because it is indexed. At 300 dpi, 6400 px is about 21 in / 54 cm wide.
- **Fewer, bolder colours:** *Windows 16 colors*, or *Adaptive* with N = 8.
- **Truer to the source colours:** *Adaptive* with N = 32 or 64.
- **Scans, line art, comics:** *Mode = Black & white*. If the automatic threshold loses thin lines, set *Threshold* (higher = more black).
- **Old-school shading:** turn on *Dither*. Despeckle is skipped automatically so the dither pattern survives.
- **Flat and clean (default):** *Dither* off, *Despeckle* on.
- **Logos and graphics with transparency:** transparent areas become white, like Paint's canvas.

## 5. Command line

Everything the page does, with the same defaults:

```bash
PYTHONPATH=app/src uv run --project app python -m red_sun ./comics --mode bw --width 800 --output-width 4000
PYTHONPATH=app/src uv run --project app python -m red_sun photo.jpg --palette adaptive --colors 12 --format both
```

`python -m red_sun` with no input opens the GUI.

## 6. Quit

Click **Quit Red Sun** in the page footer (or Ctrl+C in the terminal for `run.sh`). Closing the tab does not stop the server; launching again just reopens the running copy.

## Troubleshooting

| Symptom | What to do |
|---|---|
| macOS: "cannot be opened because the developer cannot be verified" | Right-click the `.app` → **Open** → **Open**. The launcher is not yet code-signed. |
| Windows: SmartScreen "Windows protected your PC" | **More info** → **Run anyway**. |
| Linux: double-click does nothing | File properties → Permissions → *Allow executing file as program*, then try again. |
| Setup page shows an error code | `BOOT-NETWORK`: first launch needs internet once; click Retry. `LAUNCH-ROOT`: the launcher was moved away from the `app` folder; extract the ZIP again. Other codes name the exact problem; the log path is on the page. |
| Browse buttons say the native dialog is unavailable | Type or paste the path instead. (Tk is missing from that Python; the ZIP's managed Python includes it.) |
| "No supported images found" | Check the extension list in the message; hidden files (leading dot) are skipped. |
| Output looks noisy | Make sure *Despeckle* is on and *Dither* is off. Raise the grid width if detail is being lost. |
| Colours look wrong on a logo | Turn off *Contrast boost*, or use *Adaptive*. |
| Page never loads at `127.0.0.1` | Another program may be blocking loopback; check the terminal (`run.sh`) or the launcher log for the actual port. |

Application data for the ZIP app lives in `~/Library/Application Support/Red Sun` (macOS), `%LOCALAPPDATA%\Red Sun` (Windows), `~/.local/share/Red Sun` (Linux). Deleting that folder resets the runtime; the next launch downloads it again.
