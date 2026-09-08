#!/usr/bin/env python3
"""
ms_paintify.py

Batch-process images into a crisp, low-resolution "MS Paint" raster look.

Requires:
    pip install pillow

Examples:
    python ms_paintify.py ./images
    python ms_paintify.py ./images --mode bw --width 700
    python ms_paintify.py ./images --mode color --width 700 --colors 16
    python ms_paintify.py ./images --mode color --width 700 --colors 9 --dither
    python ms_paintify.py comic.tif --mode auto --width 700 --upscale 2

Output goes into an "ms_paint" folder next to the input.
"""

import argparse
from pathlib import Path
from PIL import Image, ImageOps

SUPPORTED = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def otsu_threshold(gray: Image.Image) -> int:
    hist = gray.histogram()
    total = sum(hist)
    if total == 0:
        return 128

    sum_total = sum(i * hist[i] for i in range(256))
    sum_bg = 0
    weight_bg = 0
    best_variance = -1
    threshold = 128

    for i in range(256):
        weight_bg += hist[i]
        if weight_bg == 0:
            continue

        weight_fg = total - weight_bg
        if weight_fg == 0:
            break

        sum_bg += i * hist[i]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg

        between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if between > best_variance:
            best_variance = between
            threshold = i

    return threshold


def resize_down(img: Image.Image, width: int) -> Image.Image:
    if img.width <= width:
        return img.copy()

    height = round(img.height * width / img.width)
    return img.resize((width, height), Image.Resampling.LANCZOS)


def process_bw(img: Image.Image, width: int, threshold=None) -> Image.Image:
    img = resize_down(img, width)
    gray = img.convert("L")
    gray = ImageOps.autocontrast(gray, cutoff=0.2)

    t = otsu_threshold(gray) if threshold is None else threshold
    bw = gray.point(lambda p: 255 if p > t else 0, mode="1")
    return bw.convert("L")


def process_color(img: Image.Image, width: int, colors: int, dither: bool) -> Image.Image:
    img = resize_down(img.convert("RGB"), width)

    dither_mode = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE

    return img.quantize(
        colors=colors,
        method=Image.Quantize.MEDIANCUT,
        dither=dither_mode,
    )


def integer_upscale(img: Image.Image, factor: int) -> Image.Image:
    if factor <= 1:
        return img

    return img.resize(
        (img.width * factor, img.height * factor),
        Image.Resampling.NEAREST,
    )


def auto_mode(img: Image.Image) -> str:
    rgb = img.convert("RGB")
    thumb = rgb.copy()
    thumb.thumbnail((256, 256), Image.Resampling.LANCZOS)

    pixels = list(thumb.getdata())
    if not pixels:
        return "bw"

    mean_channel_delta = sum(
        (abs(r - g) + abs(g - b) + abs(r - b)) / 3
        for r, g, b in pixels
    ) / len(pixels)

    return "bw" if mean_channel_delta < 3.0 else "color"


def collect_files(path: Path):
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED else []

    return sorted(
        p for p in path.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED
    )


def main():
    parser = argparse.ArgumentParser(
        description="Convert images to a crisp low-res MS Paint-like raster style."
    )
    parser.add_argument("input", type=Path, help="Image file or folder")
    parser.add_argument(
        "--mode", choices=["auto", "bw", "color"], default="auto",
        help="Processing mode. Default: auto"
    )
    parser.add_argument(
        "--width", type=int, default=700,
        help="Working width before optional upscale. Default: 700"
    )
    parser.add_argument(
        "--colors", type=int, default=32,
        help="Palette size for color mode. Try 9, 16, 32, or 64. Default: 16"
    )
    parser.add_argument(
        "--dither", action="store_true",
        help="Use Floyd-Steinberg diffusion dithering in color mode"
    )
    parser.add_argument(
        "--threshold", type=int, default=None,
        help="Manual B&W threshold 0-255. Default: automatic Otsu threshold"
    )
    parser.add_argument(
        "--upscale", type=int, default=1,
        help="Integer nearest-neighbor upscale after processing. Default: 1"
    )

    args = parser.parse_args()

    files = collect_files(args.input)
    if not files:
        raise SystemExit("No supported images found.")

    base = args.input.parent if args.input.is_file() else args.input
    out_dir = base / "ms_paint"
    out_dir.mkdir(exist_ok=True)

    for src in files:
        try:
            with Image.open(src) as original:
                original.load()

                mode = auto_mode(original) if args.mode == "auto" else args.mode

                if mode == "bw":
                    result = process_bw(
                        original,
                        width=args.width,
                        threshold=args.threshold
                    )
                    tag = "bw"
                else:
                    result = process_color(
                        original,
                        width=args.width,
                        colors=args.colors,
                        dither=args.dither
                    )
                    tag = f"{args.colors}c" + ("_dither" if args.dither else "")

                result = integer_upscale(result, args.upscale)

                out = out_dir / f"{src.stem}_mspaint_{tag}.png"
                result.save(out, "PNG", optimize=True)

                print(
                    f"{src.name} -> {out.name} "
                    f"[{mode}, {result.width}x{result.height}]"
                )

        except Exception as exc:
            print(f"ERROR: {src.name}: {exc}")


if __name__ == "__main__":
    main()
