"""Red Sun image pipeline: sharp, color-indexed, MS Paint style bitmaps.

Every output pixel is one flat palette colour. The only resampling that ever
happens is the initial downscale to the pixel grid; everything after that is
palette lookup and integer nearest-neighbour upscale, so edges stay hard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

SUPPORTED = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}

# Classic Windows Paint colour box: top row, then bottom row.
PAINT_28 = [
    (0, 0, 0), (128, 128, 128), (128, 0, 0), (128, 128, 0), (0, 128, 0), (0, 128, 128), (0, 0, 128),
    (128, 0, 128), (128, 128, 64), (0, 64, 64), (0, 128, 255), (0, 64, 128), (64, 0, 255), (128, 64, 0),
    (255, 255, 255), (192, 192, 192), (255, 0, 0), (255, 255, 0), (0, 255, 0), (0, 255, 255), (0, 0, 255),
    (255, 0, 255), (255, 255, 128), (0, 255, 128), (128, 255, 255), (128, 128, 255), (255, 0, 128), (255, 128, 64),
]
WIN_16 = PAINT_28[:8] + PAINT_28[14:22]
PALETTES = {"paint": PAINT_28, "win16": WIN_16}

MODES = ("auto", "color", "bw")
PALETTE_CHOICES = ("paint", "win16", "adaptive")
FORMATS = ("png", "bmp", "both")


@dataclass
class Settings:
    mode: str = "auto"          # auto | color | bw
    palette: str = "paint"      # paint | win16 | adaptive
    colors: int = 16            # adaptive palette size
    dither: bool = False        # Floyd-Steinberg; off = hard flat regions
    despeckle: bool = True      # 5x5 median before quantizing + 3x3 mode filter after (skipped when dithering)
    contrast: bool = True       # B&W: autocontrast stretch; color: fixed hue-safe boost
    threshold: int | None = None  # B&W cut 0-255; None = Otsu
    grid_width: int = 640       # pixel grid (working) width
    output_width: int = 3200    # target output width; rounded to an integer multiple of the grid
    fmt: str = "png"            # png | bmp | both

    def validate(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if self.palette not in PALETTE_CHOICES:
            raise ValueError(f"palette must be one of {PALETTE_CHOICES}")
        if self.fmt not in FORMATS:
            raise ValueError(f"format must be one of {FORMATS}")
        if not 2 <= self.colors <= 256:
            raise ValueError("colors must be 2-256")
        if not 16 <= self.grid_width <= 4096:
            raise ValueError("pixel grid width must be 16-4096")
        if not 16 <= self.output_width <= 16384:
            raise ValueError("output width must be 16-16384")
        if self.threshold is not None and not 0 <= self.threshold <= 255:
            raise ValueError("threshold must be 0-255")

    def factor(self) -> int:
        return max(1, round(self.output_width / self.grid_width))

    def tag(self, mode: str) -> str:
        if mode == "bw":
            return "bw"
        name = {"paint": "paint28", "win16": "win16"}.get(self.palette, f"{self.colors}c")
        return name + ("_dither" if self.dither else "")


@dataclass
class Processed:
    image: Image.Image
    mode: str      # color | bw actually used
    colors: int    # distinct colours in the result


@dataclass
class Result:
    source: Path
    outputs: list[Path] = field(default_factory=list)
    width: int = 0
    height: int = 0
    colors: int = 0
    mode: str = ""
    error: str | None = None


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


def flatten(img: Image.Image) -> Image.Image:
    """Honour EXIF rotation and composite transparency onto white, like Paint's canvas."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        return Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
    return img.convert("RGB")


def resize_down(img: Image.Image, width: int) -> Image.Image:
    if img.width <= width:
        return img.copy()
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


def process(img: Image.Image, s: Settings) -> Processed:
    s.validate()
    mode = auto_mode(img) if s.mode == "auto" else s.mode
    small = resize_down(flatten(img), s.grid_width)
    # Median (edge-preserving, never blurs) kills grain before it can become speckle;
    # the mode filter afterwards removes what survives. Both skipped for dithering.
    clean = s.despeckle and not s.dither
    if clean:
        small = small.filter(ImageFilter.MedianFilter(5))
    if mode == "bw":
        gray = small.convert("L")
        if s.contrast:
            gray = ImageOps.autocontrast(gray, cutoff=0.2)
        t = otsu_threshold(gray) if s.threshold is None else s.threshold
        out = gray.point(lambda p: 255 if p > t else 0, mode="L")
        if clean:
            out = out.filter(ImageFilter.ModeFilter(3))
        out = out.point(lambda p: 255 if p > 127 else 0, mode="1")
    else:
        if s.contrast:
            # a fixed boost, not a histogram stretch: a mostly-white logo must not turn its red into black
            small = ImageEnhance.Contrast(small).enhance(1.3)
        dither = Image.Dither.FLOYDSTEINBERG if s.dither else Image.Dither.NONE
        if s.palette == "adaptive":
            out = small.quantize(colors=s.colors, method=Image.Quantize.MEDIANCUT, dither=dither)
        else:
            out = small.quantize(palette=palette_image(PALETTES[s.palette]), dither=dither)
        if clean:
            out = out.filter(ImageFilter.ModeFilter(3))
    colors = len(out.convert("RGB").getcolors(maxcolors=1 << 20) or [])
    return Processed(integer_upscale(out, s.factor()), mode, colors)


def collect_files(path: Path, recursive: bool = False) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED else []
    walk = path.rglob("*") if recursive else path.iterdir()
    return sorted(p for p in walk if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith("."))


def default_out_dir(path: Path) -> Path:
    return (path.parent if path.is_file() else path) / "red_sun"


def save(result: Image.Image, stem: Path, fmt: str) -> list[Path]:
    outputs = []
    if fmt in ("png", "both"):
        out = stem.with_suffix(".png")
        result.save(out, "PNG", optimize=True)
        outputs.append(out)
    if fmt in ("bmp", "both"):
        out = stem.with_suffix(".bmp")
        result.save(out, "BMP")
        outputs.append(out)
    return outputs


def run_batch(
    files: list[Path],
    out_dir: Path,
    s: Settings,
    progress: Callable[[int, int, Result], None] | None = None,
) -> list[Result]:
    """Process every file; one bad file never stops the batch."""
    s.validate()
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    for index, src in enumerate(files, 1):
        result = Result(source=src)
        try:
            with Image.open(src) as original:
                original.load()
                done = process(original, s)
            stem = out_dir / f"{src.stem}_redsun_{s.tag(done.mode)}"
            result.outputs = save(done.image, stem, s.fmt)
            result.width, result.height = done.image.size
            result.colors, result.mode = done.colors, done.mode
        except Exception as exc:  # noqa: BLE001 - report and continue
            result.error = f"{type(exc).__name__}: {exc}"
        results.append(result)
        if progress:
            progress(index, len(files), result)
    return results
