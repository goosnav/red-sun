"""Command line: `python -m red_sun <file-or-folder> [options]`. With no input it opens the GUI."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import core


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="red_sun", description="Sharp, color-indexed MS Paint style bitmaps. Omit INPUT to open the GUI.")
    p.add_argument("input", nargs="?", type=Path, help="image file or folder")
    p.add_argument("--mode", choices=core.MODES, default="auto")
    p.add_argument("--palette", choices=core.PALETTE_CHOICES, default="paint")
    p.add_argument("--colors", type=int, default=16, help="palette size for --palette adaptive")
    p.add_argument("--dither", action="store_true", help="Floyd-Steinberg dithering (default off: flat regions)")
    p.add_argument("--no-despeckle", action="store_true")
    p.add_argument("--no-contrast", action="store_true")
    p.add_argument("--threshold", type=int, help="black & white cut 0-255 (default: automatic)")
    p.add_argument("--width", type=int, default=640, help="pixel grid width (default 640)")
    p.add_argument("--output-width", type=int, default=3200, help="output width, rounded to a multiple of the grid (default 3200)")
    p.add_argument("--format", choices=core.FORMATS, default="png")
    p.add_argument("--recursive", action="store_true", help="include subfolders")
    p.add_argument("--out", type=Path, help="output folder (default: red_sun next to the input)")
    a = p.parse_args(argv)

    if a.input is None:
        from .start import main as gui
        return gui()

    settings = core.Settings(
        mode=a.mode, palette=a.palette, colors=a.colors, dither=a.dither, despeckle=not a.no_despeckle,
        contrast=not a.no_contrast, threshold=a.threshold, grid_width=a.width, output_width=a.output_width, fmt=a.format,
    )
    settings.validate()
    files = core.collect_files(a.input, recursive=a.recursive)
    if not files:
        p.exit(1, "No supported images found.\n")
    out_dir = a.out or core.default_out_dir(a.input)

    def report(i: int, n: int, r: core.Result) -> None:
        if r.error:
            print(f"[{i}/{n}] ERROR {r.source.name}: {r.error}")
        else:
            print(f"[{i}/{n}] {r.source.name} -> {', '.join(o.name for o in r.outputs)} [{r.mode}, {r.width}x{r.height}, {r.colors} colors]")

    results = core.run_batch(files, out_dir, settings, progress=report)
    failed = sum(1 for r in results if r.error)
    print(f"Done: {len(results) - failed} saved to {out_dir}" + (f", {failed} failed" if failed else ""))
    return 1 if failed == len(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
