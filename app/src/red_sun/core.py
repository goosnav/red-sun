"""Red Sun image pipeline: sharp, color-indexed, MS Paint style bitmaps.

The image is processed at its native resolution: every source pixel is forced into a
small exact palette (or pure black/white), the way Photoshop's Indexed Color and
Bitmap modes work. Nothing is resampled unless a pixel grid is requested, and the
only upscale is an integer nearest-neighbour multiply. Edges stay one pixel hard.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

SUPPORTED = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}

# Classic Windows Paint colour box: top row, then bottom row.
PAINT_28 = [
    (0, 0, 0), (128, 128, 128), (128, 0, 0), (128, 128, 0), (0, 128, 0), (0, 128, 128), (0, 0, 128),
    (128, 0, 128), (128, 128, 64), (0, 64, 64), (0, 128, 255), (0, 64, 128), (64, 0, 255), (128, 64, 0),
    (255, 255, 255), (192, 192, 192), (255, 0, 0), (255, 255, 0), (0, 255, 0), (0, 255, 255), (0, 0, 255),
    (255, 0, 255), (255, 255, 128), (0, 255, 128), (128, 255, 255), (128, 128, 255), (255, 0, 128), (255, 128, 64),
]
WIN_16 = PAINT_28[:8] + PAINT_28[14:22]
WEB_216 = [(r, g, b) for r in range(0, 256, 51) for g in range(0, 256, 51) for b in range(0, 256, 51)]
PALETTES = {"paint": PAINT_28, "win16": WIN_16, "websafe": WEB_216}
MATTES = {"white": (255, 255, 255), "gray": (204, 204, 204), "black": (0, 0, 0)}  # gray = Netscape gray

MODES = ("auto", "color", "bw")
PALETTE_CHOICES = ("adaptive", "paint", "win16", "websafe")
DITHERS = ("none", "diffusion", "pattern", "noise")
FORMATS = ("png", "bmp", "gif", "all")
EXTENSIONS = {"png": [".png"], "bmp": [".bmp"], "gif": [".gif"], "all": [".png", ".bmp", ".gif"]}

BAYER_8 = [
    [0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21],
]
RUN_RE = re.compile(r"red-sun-run-(\d+)$")


@dataclass
class Settings:
    mode: str = "auto"              # auto | color | bw
    palette: str = "adaptive"       # adaptive | paint | win16 | websafe
    colors: int = 16                # adaptive palette size
    dither: str = "none"            # none | diffusion | pattern | noise
    dither_strength: int = 60       # % amplitude for pattern/noise
    sharpen: bool = True            # unsharp mask before the palette snap
    despeckle: bool = False         # 3x3 median before everything else
    contrast: bool = True           # B&W: autocontrast stretch; color: fixed hue-safe boost
    threshold: int | None = None    # B&W cut 0-255; None = Otsu; 128 = Photoshop's 50%
    matte: str = "white"            # background for transparent pixels: white | gray | black
    grid_width: int | None = None   # optional downscale to a chunky pixel grid; None = native
    min_output_width: int = 3200    # integer nearest-neighbour upscale until at least this wide
    fmt: str = "png"                # png | bmp | gif | all

    def validate(self) -> None:
        checks = [
            (self.mode in MODES, f"mode must be one of {MODES}"),
            (self.palette in PALETTE_CHOICES, f"palette must be one of {PALETTE_CHOICES}"),
            (self.dither in DITHERS, f"dither must be one of {DITHERS}"),
            (self.fmt in FORMATS, f"format must be one of {FORMATS}"),
            (self.matte in MATTES, f"matte must be one of {tuple(MATTES)}"),
            (2 <= self.colors <= 256, "colors must be 2-256"),
            (0 <= self.dither_strength <= 100, "dither strength must be 0-100"),
            (self.grid_width is None or 16 <= self.grid_width <= 16384, "pixel grid width must be 16-16384"),
            (0 <= self.min_output_width <= 32768, "minimum output width must be 0-32768"),
            (self.threshold is None or 0 <= self.threshold <= 255, "threshold must be 0-255"),
        ]
        for ok, message in checks:
            if not ok:
                raise ValueError(message)

    def tag(self, mode: str) -> str:
        name = "bw" if mode == "bw" else {"paint": "paint28", "win16": "win16", "websafe": "web216"}.get(self.palette, f"{self.colors}c")
        return name if self.dither == "none" else f"{name}-{self.dither}"


@dataclass
class Processed:
    image: Image.Image
    mode: str      # color | bw actually used
    colors: int    # distinct colours in the result
    factor: int    # nearest-neighbour upscale applied


@dataclass
class Result:
    source: Path
    outputs: list[Path] = field(default_factory=list)
    width: int = 0
    height: int = 0
    colors: int = 0
    mode: str = ""
    factor: int = 1
    error: str | None = None


# ---------------------------------------------------------------- helpers

def otsu_threshold(gray: Image.Image) -> int:
    hist = gray.histogram()
    total = sum(hist)
    if total == 0:
        return 128
    sum_total = sum(i * hist[i] for i in range(256))
    sum_bg = weight_bg = 0
    best_variance, threshold = -1.0, 128
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
            best_variance, threshold = between, i
    return threshold


def flatten(img: Image.Image, matte: tuple[int, int, int]) -> Image.Image:
    """Honour EXIF rotation and composite transparency onto the matte colour."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        return Image.alpha_composite(Image.new("RGBA", rgba.size, matte), rgba).convert("RGB")
    return img.convert("RGB")


def resize_down(img: Image.Image, width: int) -> Image.Image:
    if img.width <= width:
        return img
    return img.resize((width, round(img.height * width / img.width)), Image.Resampling.LANCZOS)


def integer_upscale(img: Image.Image, factor: int) -> Image.Image:
    if factor <= 1:
        return img
    return img.resize((img.width * factor, img.height * factor), Image.Resampling.NEAREST)


def auto_mode(img: Image.Image) -> str:
    thumb = img.convert("RGB")
    thumb.thumbnail((256, 256), Image.Resampling.LANCZOS)
    pixels = list(thumb.getdata())
    if not pixels:
        return "bw"
    delta = sum((abs(r - g) + abs(g - b) + abs(r - b)) / 3 for r, g, b in pixels) / len(pixels)
    return "bw" if delta < 3.0 else "color"


def palette_image(colors: list[tuple[int, int, int]]) -> Image.Image:
    pal = Image.new("P", (1, 1))
    flat = [c for rgb in colors for c in rgb]
    pal.putpalette(flat + flat[:3] * (256 - len(colors)))  # pad with the first colour, never a stray black
    return pal


def used_colors(p_img: Image.Image) -> list[tuple[int, int, int]]:
    pal = p_img.getpalette()
    return [tuple(pal[3 * i:3 * i + 3]) for _, i in sorted(p_img.getcolors(256), key=lambda c: c[1])]


def count_colors(img: Image.Image) -> int:
    return len(img.convert("RGB").getcolors(maxcolors=1 << 20) or [])


def bayer(size: tuple[int, int], spread: float) -> Image.Image:
    """8x8 ordered-dither threshold pattern, centred on 128, built with bytes ops (fast at any size)."""
    w, h = size
    rows = []
    for row in BAYER_8:
        vals = bytes(int(round(128 + ((v + 0.5) / 64 - 0.5) * spread)) for v in row)
        rows.append((vals * (w // 8 + 1))[:w])
    data = (b"".join(rows) * (h // 8 + 1))[: w * h]
    return Image.frombytes("L", (w, h), data)


def dither_offset(size: tuple[int, int], s: Settings) -> Image.Image | None:
    spread = 255 * s.dither_strength / 100
    if s.dither == "pattern":
        return bayer(size, spread)
    if s.dither == "noise":
        return Image.effect_noise(size, spread / 4)
    return None


def add_offset(img: Image.Image, offset: Image.Image) -> Image.Image:
    """img + (offset - 128), clipped. Ordered/noise dithering = add a threshold field, then snap without diffusion."""
    if img.mode == "RGB":
        offset = Image.merge("RGB", (offset, offset, offset))
    return ImageChops.add(img, offset, 1.0, -128)


# ---------------------------------------------------------------- pipeline

def prepare(img: Image.Image, s: Settings) -> Image.Image:
    rgb = flatten(img, MATTES[s.matte])
    if s.grid_width:
        rgb = resize_down(rgb, s.grid_width)  # the ONLY resampling, and only on request
    if s.despeckle:
        rgb = rgb.filter(ImageFilter.MedianFilter(3))
    if s.sharpen:
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=2))
    return rgb


def quantize_color(rgb: Image.Image, s: Settings) -> Image.Image:
    if s.contrast:
        rgb = ImageEnhance.Contrast(rgb).enhance(1.3)  # fixed and hue-safe: a mostly-white logo keeps its red
    if s.palette == "adaptive":
        base = rgb.quantize(colors=s.colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        colors = used_colors(base)
    else:
        colors = PALETTES[s.palette]
    offset = dither_offset(rgb.size, s)
    if offset is not None:
        rgb = add_offset(rgb, offset)
    diffusion = Image.Dither.FLOYDSTEINBERG if s.dither == "diffusion" else Image.Dither.NONE
    return rgb.quantize(palette=palette_image(colors), dither=diffusion)


def quantize_bw(rgb: Image.Image, s: Settings) -> Image.Image:
    gray = rgb.convert("L")
    if s.contrast:
        gray = ImageOps.autocontrast(gray, cutoff=0.2)
    t = otsu_threshold(gray) if s.threshold is None else s.threshold
    offset = dither_offset(gray.size, s)
    if offset is not None:
        gray = add_offset(gray, offset)
    if s.dither == "diffusion":
        shifted = gray.point(lambda p: min(255, max(0, p + 128 - t)))  # Pillow diffuses around 128
        return shifted.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    return gray.point(lambda p: 255 if p > t else 0, mode="1")


def process(img: Image.Image, s: Settings) -> Processed:
    s.validate()
    mode = auto_mode(img) if s.mode == "auto" else s.mode
    rgb = prepare(img, s)
    out = quantize_bw(rgb, s) if mode == "bw" else quantize_color(rgb, s)
    factor = max(1, -(-s.min_output_width // out.width))  # ceil: never resample, only multiply
    return Processed(integer_upscale(out, factor), mode, count_colors(out), factor)


# ---------------------------------------------------------------- files and runs

def is_image(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith(".")


def collect_files(path: Path, recursive: bool = False) -> list[Path]:
    """Images in a folder (or the single file). Recursive walks skip hidden folders and earlier run folders."""
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED else []
    if not recursive:
        return sorted(p for p in path.iterdir() if is_image(p))
    found = []
    for p in path.rglob("*"):
        parts = p.relative_to(path).parts[:-1]
        if any(part.startswith(".") or RUN_RE.match(part) for part in parts):
            continue
        if is_image(p):
            found.append(p)
    return sorted(found)


def source_folder(path: Path) -> Path:
    return path.parent if path.is_file() else path


def next_run_dir(root: Path) -> Path:
    """red-sun-run-001, -002, ... inside root (not created here)."""
    numbers = [int(m.group(1)) for p in root.glob("red-sun-run-*") if p.is_dir() and (m := RUN_RE.match(p.name))]
    return root / f"red-sun-run-{max(numbers, default=0) + 1:03d}"


def save(result: Image.Image, stem: Path, fmt: str) -> list[Path]:
    outputs = []
    for ext in EXTENSIONS[fmt]:
        out = stem.with_suffix(ext)
        if ext == ".gif" and result.mode == "1":
            result.convert("L").convert("P", palette=Image.Palette.ADAPTIVE, colors=2).save(out, "GIF")
        else:
            result.save(out, {".png": "PNG", ".bmp": "BMP", ".gif": "GIF"}[ext], optimize=True)
        outputs.append(out)
    return outputs


def run_batch(
    files: list[Path],
    out_dir: Path,
    s: Settings,
    progress: Callable[[int, int, Result], None] | None = None,
) -> list[Result]:
    """Process every file into out_dir; one bad file never stops the batch."""
    s.validate()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "settings.json").write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
    results: list[Result] = []
    used: set[str] = set()
    for index, src in enumerate(files, 1):
        result = Result(source=src)
        try:
            with Image.open(src) as original:
                original.load()
                done = process(original, s)
            base = stem = f"{src.stem}_redsun_{s.tag(done.mode)}"
            k = 2
            while stem in used:  # same file name from two folders in one batch
                stem, k = f"{base}-{k}", k + 1
            used.add(stem)
            result.outputs = save(done.image, out_dir / stem, s.fmt)
            result.width, result.height = done.image.size
            result.colors, result.mode, result.factor = done.colors, done.mode, done.factor
        except Exception as exc:  # noqa: BLE001 - report and continue
            result.error = f"{type(exc).__name__}: {exc}"
        results.append(result)
        if progress:
            progress(index, len(files), result)
    return results
