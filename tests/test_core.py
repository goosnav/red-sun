"""Core pipeline checks. Run: uv run --project app --group dev pytest"""

import random
from pathlib import Path

import pytest
from PIL import Image, ImageFilter

from red_sun import core

random.seed(7)


def photo(width=1000, height=700, gray=False) -> Image.Image:
    """Noisy gradient that behaves like a photo (many colours, soft edges)."""
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        for x in range(width):
            r = (x * 255 // width + random.randint(-12, 12)) % 256
            g = (y * 255 // height + random.randint(-12, 12)) % 256
            b = ((x + y) * 255 // (width + height) + random.randint(-12, 12)) % 256
            px[x, y] = (r, r, r) if gray else (r, g, b)
    return img


def rgb_colors(img: Image.Image) -> set[tuple[int, int, int]]:
    return {c for _, c in img.convert("RGB").getcolors(maxcolors=1 << 20)}


def flat_blocks(img: Image.Image, factor: int, samples=((0, 0), (37, 12), (150, 90))) -> bool:
    px = img.convert("RGB").load()
    return all(len({px[bx * factor + dx, by * factor + dy] for dx in range(factor) for dy in range(factor)}) == 1 for bx, by in samples)


def test_native_resolution_is_kept_and_only_palette_colors_remain():
    s = core.Settings(mode="color", palette="paint", min_output_width=0)
    done = core.process(photo(), s)
    assert done.image.size == (1000, 700) and done.factor == 1      # no resampling at all
    assert done.image.mode == "P"
    assert rgb_colors(done.image) <= set(core.PAINT_28)
    assert 2 < done.colors <= 28


def test_small_source_is_multiplied_not_resampled():
    s = core.Settings(mode="color", palette="paint", min_output_width=1000)
    done = core.process(photo(200, 140), s)
    assert done.factor == 5 and done.image.size == (1000, 700)
    assert flat_blocks(done.image, 5)


def test_pixel_grid_downsamples_once():
    s = core.Settings(mode="color", palette="win16", grid_width=250, min_output_width=1000)
    done = core.process(photo(), s)
    assert done.image.size == (1000, 700) and done.factor == 4
    assert rgb_colors(done.image) <= set(core.WIN_16)


@pytest.mark.parametrize("dither", core.DITHERS)
def test_every_dither_mode_stays_inside_the_palette(dither):
    s = core.Settings(mode="color", palette="websafe", dither=dither, dither_strength=80, min_output_width=0)
    done = core.process(photo(300, 200), s)
    assert rgb_colors(done.image) <= set(core.WEB_216)
    s = core.Settings(mode="color", palette="adaptive", colors=8, dither=dither, min_output_width=0)
    assert core.process(photo(300, 200), s).colors <= 8


@pytest.mark.parametrize("dither", core.DITHERS)
def test_bw_modes_are_pure_black_and_white(dither):
    s = core.Settings(mode="bw", dither=dither, min_output_width=0)
    done = core.process(photo(300, 200), s)
    assert done.image.mode == "1" and done.colors == 2
    s = core.Settings(mode="auto", min_output_width=0)
    assert core.process(photo(300, 200, gray=True), s).mode == "bw"
    assert core.process(photo(300, 200), s).mode == "color"


def test_pattern_dither_actually_dithers_and_bayer_is_tiled():
    tile = core.bayer((16, 16), 255)
    px = tile.load()
    assert px[0, 0] == px[8, 8] and px[0, 0] != px[1, 0]           # 8x8 period, non-constant
    flat = Image.new("L", (64, 64), 128)
    s = core.Settings(mode="bw", dither="pattern", dither_strength=100, threshold=128, contrast=False, sharpen=False, min_output_width=0)
    assert core.process(flat, s).colors == 2                         # mid gray becomes a pattern
    s.dither = "none"
    assert core.process(flat, s).colors == 1                         # without dither it is one solid tone


def test_transparency_uses_the_matte():
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    s = core.Settings(mode="color", palette="websafe", matte="gray", contrast=False, sharpen=False, min_output_width=0)
    assert rgb_colors(core.process(img, s).image) == {(204, 204, 204)}


def test_logo_red_survives_contrast_boost():
    """A mostly-white graphic must keep its red; a histogram stretch would turn it black."""
    img = Image.new("RGB", (400, 400), "white")
    for y in range(100, 300):
        for x in range(100, 300):
            img.putpixel((x, y), (200, 30, 30))
    s = core.Settings(mode="color", palette="paint", min_output_width=0)
    assert (255, 0, 0) in rgb_colors(core.process(img, s).image)


def test_edges_snap_hard_and_sharpen_narrows_the_ramp():
    """A blurred edge: with a 2-colour palette it is exactly one step; sharpening shortens the ramp otherwise."""
    img = Image.new("RGB", (200, 100), "white")
    for y in range(100):
        for x in range(100):
            img.putpixel((x, y), (0, 0, 0))
    soft = img.filter(ImageFilter.GaussianBlur(3))

    def row(settings):
        px = core.process(soft, settings).image.convert("RGB").load()
        return [px[x, 50] for x in range(200)]

    two = row(core.Settings(mode="color", palette="adaptive", colors=2, min_output_width=0))
    assert len(set(two)) == 2                                        # two colours only
    assert sum(1 for a, b in zip(two, two[1:]) if a != b) == 1     # exactly one transition, nothing in between

    def ramp(settings):  # pixels that are neither pure black nor pure white
        return sum(1 for c in row(settings) if c not in ((0, 0, 0), (255, 255, 255)))

    sharp = ramp(core.Settings(mode="color", palette="win16", sharpen=True, min_output_width=0))
    soft_ramp = ramp(core.Settings(mode="color", palette="win16", sharpen=False, min_output_width=0))
    assert 0 < sharp < soft_ramp


def test_batch_numbered_runs_and_naming(tmp_path: Path):
    src = tmp_path / "in"
    src.mkdir()
    photo(300, 200).save(src / "a.png")
    photo(300, 200, gray=True).save(src / "b.jpg")
    (src / "broken.png").write_bytes(b"not an image")
    (src / "notes.txt").write_text("ignored")
    (src / ".hidden.png").write_bytes(b"skip")
    files = core.collect_files(src)
    assert [f.name for f in files] == ["a.png", "b.jpg", "broken.png"]

    root = tmp_path / "exports"
    assert core.next_run_dir(root) == root / "red-sun-run-001"
    (root / "red-sun-run-007").mkdir(parents=True)
    out = core.next_run_dir(root)
    assert out.name == "red-sun-run-008"

    s = core.Settings(min_output_width=900, fmt="all")
    seen = []
    results = core.run_batch(files + [src / "a.png"], out, s, progress=lambda i, n, r: seen.append((i, n)))
    assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)]
    ok = [r for r in results if not r.error]
    assert len(ok) == 3 and results[2].error
    assert {p.name for p in ok[0].outputs} == {"a_redsun_16c.png", "a_redsun_16c.bmp", "a_redsun_16c.gif"}
    assert {p.name for p in ok[1].outputs} == {"b_redsun_bw.png", "b_redsun_bw.bmp", "b_redsun_bw.gif"}
    assert {p.name for p in ok[2].outputs} == {"a_redsun_16c-2.png", "a_redsun_16c-2.bmp", "a_redsun_16c-2.gif"}
    assert (out / "settings.json").exists()
    with Image.open(out / "a_redsun_16c.bmp") as bmp:
        assert bmp.mode == "P" and bmp.size == (900, 600)
    with Image.open(out / "b_redsun_bw.png") as png:
        assert png.mode == "1"
    with Image.open(out / "b_redsun_bw.gif") as gif:
        assert gif.mode == "P" and len(gif.getcolors()) == 2


def test_settings_validation():
    with pytest.raises(ValueError):
        core.Settings(palette="rainbow").validate()
    with pytest.raises(ValueError):
        core.Settings(dither="magic").validate()
    with pytest.raises(ValueError):
        core.Settings(grid_width=5).validate()
    core.Settings().validate()
    assert core.Settings().tag("color") == "16c"
    assert core.Settings(palette="paint", dither="pattern").tag("color") == "paint28-pattern"
