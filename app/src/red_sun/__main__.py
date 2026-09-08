"""Command line: `python -m red_sun <file-or-folder> [options]`. With no input it opens the GUI."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import core


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="red_sun", description="Sharp, color-indexed MS Paint style bitmaps. Omit INPUT to open the GUI.")
    p.add_argument("input", nargs="?", type=Path, help="image file or folder")
    p.add_argument("--mode", choices=core.MODES, default="auto")
    p.add_argument("--palette", choices=core.PALETTE_CHOICES, default="adaptive")
    p.add_argument("--colors", type=int, default=16, help="palette size for --palette adaptive (default 16)")
    p.add_argument("--dither", choices=core.DITHERS, default="none")
    p.add_argument("--dither-strength", type=int, default=60, help="%% amplitude for pattern/noise (default 60)")
    p.add_argument("--no-sharpen", action="store_true", help="skip the unsharp mask before the palette snap")
    p.add_argument("--no-contrast", action="store_true")
    p.add_argument("--despeckle", action="store_true", help="3x3 median before processing")
    p.add_argument("--threshold", type=int, help="black & white cut 0-255 (default: automatic; 128 = Photoshop 50%%)")
    p.add_argument("--matte", choices=tuple(core.MATTES), default="white", help="background for transparent pixels")
    p.add_argument("--grid", type=int, help="downsample once to this pixel-grid width (default: keep native resolution)")
    p.add_argument("--min-output-width", type=int, default=3200, help="nearest-neighbour multiply until at least this wide (default 3200; 0 = never)")
    p.add_argument("--format", choices=core.FORMATS, default="png")
    p.add_argument("--recursive", action="store_true", help="include subfolders")
    p.add_argument("--out", type=Path, help="output folder (default: the next red-sun-run-NNN folder)")
    a = p.parse_args(argv)

    if a.input is None:
        from .start import main as gui
        return gui()

    settings = core.Settings(
        mode=a.mode, palette=a.palette, colors=a.colors, dither=a.dither, dither_strength=a.dither_strength,
        sharpen=not a.no_sharpen, despeckle=a.despeckle, contrast=not a.no_contrast, threshold=a.threshold,
        matte=a.matte, grid_width=a.grid, min_output_width=a.min_output_width, fmt=a.format,
    )
    settings.validate()
    files = core.collect_files(a.input, recursive=a.recursive)
    if not files:
        p.exit(1, "No supported images found.\n")
    out_dir = a.out or core.next_run_dir()

    def report(i: int, n: int, r: core.Result) -> None:
        if r.error:
            print(f"[{i}/{n}] ERROR {r.source.name}: {r.error}")
        else:
            scale = f" x{r.factor}" if r.factor > 1 else ""
            print(f"[{i}/{n}] {r.source.name} -> {', '.join(o.name for o in r.outputs)} [{r.mode}, {r.width}x{r.height}{scale}, {r.colors} colors]")

    results = core.run_batch(files, out_dir, settings, progress=report)
    failed = sum(1 for r in results if r.error)
    print(f"Done: {len(results) - failed} saved to {out_dir}" + (f", {failed} failed" if failed else ""))
    return 1 if failed == len(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
