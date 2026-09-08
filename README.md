# Red Sun

<img src="app/static/icon.png" width="96" height="96" alt="Red Sun icon: a pixelated red sun with orange and yellow rings on black">

Turn any photo into a sharp, color-indexed **MS Paint style bitmap**. Big enough to print as a poster, clean enough to post.

- **Hard pixels, no smoothing.** The image is reduced to a pixel grid, snapped to a palette, then scaled up by a whole number with nearest-neighbour. Every block is one flat colour. No gaussian blur, no anti-aliasing, no in-between shades.
- **Real palettes.** Classic MS Paint 28 colours, Windows 16 colours, or an adaptive best-N palette. Black & white with automatic (Otsu) thresholding for scans and line art.
- **Poster sized.** Default output is 3200 px wide (a 640-pixel grid at 5×). Set 6400 or 12800 for print; the file stays a tiny indexed PNG or BMP.
- **Batch.** Point it at a folder (optionally with subfolders); one bad file never stops the rest. Outputs land in a `red_sun` folder next to the originals. Originals are never touched.
- **A browser GUI that looks like 1999 on purpose.** Plain HTML forms, zero JavaScript, native file/folder picker. Also a CLI with the same options.

| Before | After (MS Paint 28, 640 grid, 5×) |
|---|---|
| ![source photo](docs/sample-before.jpg) | ![Red Sun output](docs/sample-after.png) |

## Run it

### Option A: the app (no Python needed)

Download the release ZIP, extract it completely, and double-click the launcher for your system:

| System | Launcher |
|---|---|
| macOS (Intel and Apple Silicon) | `Open Red Sun — macOS.app` |
| Windows x64 | `Open Red Sun — Windows x64.exe` |
| Windows ARM64 | `Open Red Sun — Windows ARM64.exe` |
| Linux x86_64 | `Open Red Sun — Linux x86_64.AppImage` |
| Linux ARM64 | `Open Red Sun — Linux ARM64.AppImage` |

The first launch opens a setup page in your browser, downloads a private Python runtime and the one locked dependency (Pillow) into your application-data folder, then opens the Red Sun page. Later launches are instant and offline. Keep the launcher next to the `app` folder.

**Release status.** The ZIP built and tested so far on this project covers **macOS** (launch verified) and **Windows x64 / ARM64** (built, not yet run on a Windows machine). Linux AppImages need a Linux build host and are not in the current ZIP. Unsigned launchers can trigger macOS Gatekeeper ("unidentified developer": right-click → Open) or Windows SmartScreen ("More info → Run anyway").

### Option B: from this repository

Needs [uv](https://docs.astral.sh/uv/) **or** Python 3.10+.

```bash
./run.sh          # macOS / Linux   (or double-click run.command on macOS)
```

```powershell
.\run.ps1         # Windows
```

Your browser opens at `http://127.0.0.1:<port>`. Quit from the page footer or with Ctrl+C.

### Option C: command line

```bash
PYTHONPATH=app/src uv run --project app python -m red_sun ./scans --palette paint --width 640 --output-width 3200
```

```
python -m red_sun <file-or-folder> [--mode auto|color|bw] [--palette paint|win16|adaptive] [--colors N]
                  [--dither] [--no-despeckle] [--no-contrast] [--threshold 0-255]
                  [--width GRID] [--output-width PX] [--format png|bmp|both] [--recursive] [--out DIR]
```

## The knobs

| Setting | Default | What it does |
|---|---|---|
| Mode | Auto | Auto picks black & white for grayscale sources, colour otherwise. |
| Palette | MS Paint classic (28) | Fixed palettes give the authentic look. Adaptive picks the best N colours from the image. |
| Dither | off | Floyd–Steinberg speckle shading. Off keeps regions flat and hard. |
| Despeckle | on | 5×5 median before quantizing and a 3×3 mode filter after. Removes grain and stray pixels without softening an edge. |
| Contrast boost | on | Black & white: autocontrast stretch. Colour: a fixed, hue-safe 1.3× boost (a mostly-white logo keeps its red). |
| Threshold | automatic | Manual black & white cut, 0–255. |
| Pixel grid width | 640 | How many "Paint pixels" across. 320 is chunky, 1024 is fine. |
| Output width | 3200 | Rounded to a whole multiple of the grid so blocks stay exact. 640 → 3200 is 5×. |
| Format | PNG | Indexed PNG, indexed BMP, or both. |

Output names: `<name>_redsun_paint28.png`, `<name>_redsun_win16.png`, `<name>_redsun_16c.png`, `<name>_redsun_bw.png` (plus `_dither`).

## Develop

```bash
uv run --project app --group dev pytest        # unit tests (tests/test_core.py)
uv run --project app python tests/smoke.py     # real HTTP end-to-end smoke
python3 packaging/build_release.py             # icons, uv tools, launcher images, ZIP (see below)
```

`packaging/build_release.py` builds every launcher image the current host can build (macOS needs a Mac, Windows needs `go-winres`, Linux needs `appimagetool` on a Linux host), stages the release root, and writes either the official universal ZIP (all five images) or a clearly named `PARTIAL` ZIP. Requires Go and uv on the build machine; customers need nothing.

See [ARCHITECTURE.txt](ARCHITECTURE.txt) for how the pieces fit, and [TUTORIAL.md](TUTORIAL.md) for a walkthrough and troubleshooting.

## License

Copyright © 2026 Goosnav LLC. All rights reserved. See [LICENSE](LICENSE).
