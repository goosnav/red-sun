"""Red Sun image pipeline: sharp, color-indexed, MS Paint style bitmaps.

The image is processed at its native resolution: every source pixel is forced into a
small exact palette (or pure black/white), the way Photoshop's Indexed Color and
Bitmap modes work. Nothing is resampled unless a pixel grid is requested, and the
only upscale is an integer nearest-neighbour multiply. Edges stay one pixel hard.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
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
PALETTE_CHOICES = ("dominant", "adaptive", "paint", "win16", "websafe")
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
    palette: str = "dominant"       # dominant | adaptive | paint | win16 | websafe
    colors: int = 16                # bucket cap for dominant, palette size for adaptive
    dither: str = "none"            # none | diffusion | pattern | noise
    dither_strength: int = 60       # % amplitude for pattern/noise
    sharpen: bool = False           # unsharp mask before the palette snap (photos; halos on flat art)
    despeckle: bool = False         # 3x3 median before everything else
    contrast: bool = False          # B&W: autocontrast stretch; color: fixed hue-safe boost
    threshold: int | None = None    # B&W cut 0-255; None = Otsu; 128 = Photoshop's 50%
    # the same look knobs for images that come out black & white (auto mode decides per image)
    bw_dither: str = "none"
    bw_dither_strength: int = 60
    bw_sharpen: bool = False
    bw_despeckle: bool = False
    bw_contrast: bool = False
    matte: str = "white"            # background for transparent pixels: white | gray | black
    grid_width: int | None = None   # optional downscale to a chunky pixel grid; None = native
    min_output_width: int = 3200    # integer nearest-neighbour upscale until at least this wide
    fmt: str = "png"                # png | bmp | gif | all

    def validate(self) -> None:
        checks = [
            (self.mode in MODES, f"mode must be one of {MODES}"),
            (self.palette in PALETTE_CHOICES, f"palette must be one of {PALETTE_CHOICES}"),
            (self.dither in DITHERS, f"dither must be one of {DITHERS}"),
            (self.bw_dither in DITHERS, f"B&W dither must be one of {DITHERS}"),
            (0 <= self.bw_dither_strength <= 100, "B&W dither strength must be 0-100"),
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

    def for_mode(self, mode: str) -> "Settings":
        """The look knobs that apply to this image: the B&W set when it is black & white."""
        if mode != "bw":
            return self
        return replace(self, dither=self.bw_dither, dither_strength=self.bw_dither_strength,
                       sharpen=self.bw_sharpen, despeckle=self.bw_despeckle, contrast=self.bw_contrast)

    def tag(self, mode: str) -> str:
        names = {"paint": "paint28", "win16": "win16", "websafe": "web216", "adaptive": f"{self.colors}c", "dominant": f"flat{self.colors}"}
        look = self.for_mode(mode)
        name = "bw" if mode == "bw" else names[self.palette]
        return name if look.dither == "none" else f"{name}-{look.dither}"


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


MIN_BUCKET_SHARE = 0.002   # a colour must fill 0.2% of the flat pixels to earn a bucket
MIN_BUCKET_DISTANCE = 24   # RGB distance below which two colours are the same bucket (near-identical shades merge)
FLAT_TOLERANCE = 24        # a pixel is "flat" when its 3x3 neighbourhood varies less than this per channel
RIDGE_WIDTH = 3            # strokes up to this many pixels wide still vote for their colour
BLEND_SAT = 32             # max-min channel spread below which a pixel can be an ink/paper blend
BLEND_PROTECT = 16         # a pixel this close (per channel) to its nearest bucket is that colour, not a blend
PAPER_LUMA = 200           # light neutral buckets (paper, white) that blends may resolve to, besides ink


def channel_max(rgb: Image.Image) -> Image.Image:
    r, g, b = rgb.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def vote_mask(rgb: Image.Image) -> Image.Image:
    """255 for pixels that are a colour of the artwork, 0 for edge blends.

    A pixel votes when its 3x3 neighbourhood is one colour (flat fill, thick stroke) or when it sits in a
    stroke up to RIDGE_WIDTH pixels wide: the pixels just outside the run, on both sides, match each other
    but not it (ink on paper). An anti-aliased edge pixel is neither: the two sides of a ramp differ.
    """
    spread = channel_max(ImageChops.subtract(rgb.filter(ImageFilter.MaxFilter(3)), rgb.filter(ImageFilter.MinFilter(3))))
    votes = spread.point(lambda v: 255 if v < FLAT_TOLERANCE else 0)
    for dx, dy in ((1, 0), (0, 1)):
        for width in range(1, RIDGE_WIDTH + 1):
            for i in range(width):  # the pixel is the i-th of a run of `width`
                before = ImageChops.offset(rgb, (i + 1) * dx, (i + 1) * dy)
                after = ImageChops.offset(rgb, -(width - i) * dx, -(width - i) * dy)
                sides_match = channel_max(ImageChops.difference(before, after)).point(lambda v: 255 if v < FLAT_TOLERANCE else 0)
                stands_out = channel_max(ImageChops.difference(rgb, before)).point(lambda v: 255 if v >= FLAT_TOLERANCE else 0)
                run = ImageChops.multiply(sides_match, stands_out)
                for k in range(-i, width - i):  # every pixel of the run must be the same colour as this one
                    if k:
                        member = ImageChops.offset(rgb, -k * dx, -k * dy)
                        same = channel_max(ImageChops.difference(rgb, member)).point(lambda v: 255 if v < FLAT_TOLERANCE else 0)
                        run = ImageChops.multiply(run, same)
                votes = ImageChops.lighter(votes, run)
    return votes


def dominant_palette(rgb: Image.Image, max_colors: int) -> list[tuple[int, int, int]]:
    """The image's real flat colours, most common first: the Photoshop Indexed-Color look for comics and art.

    Only pixels inside flat regions vote (an edge pixel is a blend, not a colour of the artwork). Votes are
    binned at 16 levels per channel; bins are taken by popularity, merged when closer than
    MIN_BUCKET_DISTANCE, and ignored below MIN_BUCKET_SHARE. Near-black/near-white snap to pure.
    """
    small = rgb
    while small.width * small.height > 1_500_000:  # subsample, never blend: no invented colours
        small = small.resize((small.width // 2, small.height // 2), Image.Resampling.NEAREST)
    mask = vote_mask(small)
    if mask.histogram()[255] >= 0.02 * small.width * small.height:
        rgba = Image.merge("RGBA", (*small.split(), mask))
        votes = [(n, (r, g, b)) for n, (r, g, b, a) in rgba.getcolors(small.width * small.height) if a == 255]
    else:  # nothing is flat (a very noisy photo): every pixel votes
        votes = small.getcolors(small.width * small.height)
    total = sum(n for n, _ in votes)
    bins: dict[tuple[int, int, int], list[int]] = {}
    for count, (r, g, b) in votes:
        acc = bins.setdefault((r >> 4, g >> 4, b >> 4), [0, 0, 0, 0])
        acc[0] += count
        acc[1] += count * r
        acc[2] += count * g
        acc[3] += count * b
    candidates = sorted(((n, (sr / n, sg / n, sb / n)) for n, sr, sg, sb in bins.values()), reverse=True)
    chosen: list[tuple[float, float, float]] = []
    # Forced black and white (as in Photoshop): ink and paper are never lost, even as hairlines.
    everyone = small.getcolors(small.width * small.height)
    pixels = small.width * small.height
    for pure, near in (((0.0, 0.0, 0.0), lambda c: max(c) < 16), ((255.0, 255.0, 255.0), lambda c: min(c) >= 240)):
        if sum(n for n, c in everyone if near(c)) >= MIN_BUCKET_SHARE * pixels:
            chosen.append(pure)
    for n, col in candidates:
        if len(chosen) >= 2 and n / total < MIN_BUCKET_SHARE:
            break
        if all(sum((a - b) ** 2 for a, b in zip(col, c)) >= MIN_BUCKET_DISTANCE ** 2 for c in chosen):
            chosen.append(col)
        if len(chosen) == max_colors:
            break
    palette: list[tuple[int, int, int]] = []
    for col in chosen:
        if max(col) <= 40:          # ink is ink
            col = (0.0, 0.0, 0.0)
        elif min(col) >= 244:       # bright white paper (cream stays cream)
            col = (255.0, 255.0, 255.0)
        rounded = tuple(int(round(v)) for v in col)
        if rounded not in palette:
            palette.append(rounded)
    return palette


def luma(c: tuple[int, int, int]) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def blend_snap(out: Image.Image, rgb: Image.Image, colors: list[tuple[int, int, int]]) -> Image.Image:
    """Ink/paper blends become ink or paper. A gray-ish pixel that is not close to any bucket is a blend
    (a hairline core, an anti-alias step); it may only resolve to black or a light neutral bucket, never to
    the night-sky navy or the brown that happens to be nearest in RGB. Real bucket colours are untouched."""
    targets = [i for i, c in enumerate(colors) if c == (0, 0, 0) or (max(c) - min(c) <= FLAT_TOLERANCE and luma(c) >= PAPER_LUMA)]
    if len(targets) < 2 or len(targets) == len(colors):
        return out
    r, g, b = rgb.split()
    spread = ImageChops.subtract(ImageChops.lighter(ImageChops.lighter(r, g), b), ImageChops.darker(ImageChops.darker(r, g), b))
    grayish = spread.point(lambda v: 255 if v < BLEND_SAT else 0)
    nearest = out.convert("RGB")
    far = channel_max(ImageChops.difference(rgb, nearest)).point(lambda v: 255 if v >= BLEND_PROTECT else 0)
    sub = rgb.quantize(palette=palette_image([colors[i] for i in targets]), dither=Image.Dither.NONE)
    lut = [targets[k] if k < len(targets) else targets[0] for k in range(256)]
    out.paste(Image.frombytes("P", sub.size, bytes(lut[k] for k in sub.getdata())), None, ImageChops.multiply(grayish, far))
    return out


def snap_edges(q: Image.Image, rgb: Image.Image, mask: Image.Image, colors: list[tuple[int, int, int]]) -> Image.Image:
    """Re-snap edge pixels (mask 0) to the nearest bucket among their trusted neighbours, growing inward.

    A blend between ink and paper is globally nearest to whatever mid-tone the image happens to have
    (a brown fringe on every letter); it can only belong to one of the two sides it sits between.
    Pixels snapped in one pass become trusted for the next, so wider blend bands resolve from both sides.
    """
    w, h = q.size
    qpx, spx = q.load(), rgb.load()
    n = len(colors)
    trusted = bytearray(mask.getdata())
    pending = [i for i, v in enumerate(trusted) if not v]
    for _ in range(6):
        if not pending:
            break
        decided = []
        for i in pending:
            x, y = i % w, i // w
            r, g, b = spx[x, y]
            best, best_d = None, None
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and trusted[ny * w + nx]:
                        c = qpx[nx, ny]
                        cr, cg, cb = colors[c if c < n else 0]
                        d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
                        if best_d is None or d < best_d:
                            best, best_d = c, d
            if best is not None:
                decided.append((i, best))
        if not decided:
            break
        for i, best in decided:
            qpx[i % w, i // w] = best
            trusted[i] = 1
        done = {i for i, _ in decided}
        pending = [i for i in pending if i not in done]
    return q


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
    if s.palette == "dominant":
        colors = dominant_palette(rgb, s.colors)
    elif s.palette == "adaptive":
        base = rgb.quantize(colors=s.colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        colors = used_colors(base)
    else:
        colors = PALETTES[s.palette]
    offset = dither_offset(rgb.size, s)
    if offset is not None:
        rgb = add_offset(rgb, offset)
    diffusion = Image.Dither.FLOYDSTEINBERG if s.dither == "diffusion" else Image.Dither.NONE
    out = rgb.quantize(palette=palette_image(colors), dither=diffusion)
    if s.palette == "dominant" and s.dither == "none":
        out = blend_snap(out, rgb, colors)
        anchors = vote_mask(rgb)
        if anchors.histogram()[255] >= 0.02 * rgb.width * rgb.height:  # flat art: edges belong to one of their two sides
            out = snap_edges(out, rgb, anchors, colors)
    return out


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
    look = s.for_mode(mode)
    rgb = prepare(img, look)
    out = quantize_bw(rgb, look) if mode == "bw" else quantize_color(rgb, look)
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
        out = stem.with_name(stem.name + ext)  # not with_suffix: "08.30.2025 Comic 11" would become "08.30.png"
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
