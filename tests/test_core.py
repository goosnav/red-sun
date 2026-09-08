"""Core pipeline checks. Run: uv run --project app pytest"""

import random
from pathlib import Path

from PIL import Image

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


def test_fixed_palette_output_is_indexed_sharp_and_large():
    s = core.Settings(mode="color", palette="paint", grid_width=200, output_width=1000)
    done = core.process(photo(), s)
    assert done.image.mode == "P"
    assert done.image.size == (1000, 700)           # 200 grid x 5 nearest-neighbour
    assert rgb_colors(done.image) <= set(core.PAINT_28)
    assert 2 < done.colors <= 28
    # every 5x5 block is one flat colour: sample the block corners
    px = done.image.convert("RGB").load()
    for bx, by in [(0, 0), (37, 12), (199, 139)]:
        block = {px[bx * 5 + dx, by * 5 + dy] for dx in range(5) for dy in range(5)}
        assert len(block) == 1


def test_win16_subset_and_adaptive_limit():
    s = core.Settings(mode="color", palette="win16", grid_width=160, output_width=160)
    assert rgb_colors(core.process(photo(), s).image) <= set(core.WIN_16)
    s = core.Settings(mode="color", palette="adaptive", colors=8, grid_width=160, output_width=160)
    assert core.process(photo(), s).colors <= 8


def test_bw_and_auto_mode():
    s = core.Settings(mode="auto", grid_width=160, output_width=320)
    done = core.process(photo(gray=True), s)
    assert done.mode == "bw" and done.image.mode == "1" and done.colors == 2
    assert done.image.size == (320, 224)
    assert core.process(photo(), s).mode == "color"


def test_transparency_lands_on_white():
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    s = core.Settings(mode="color", grid_width=100, output_width=100, despeckle=False, contrast=False)
    assert rgb_colors(core.process(img, s).image) == {(255, 255, 255)}


def test_batch_skips_bad_files_and_names_outputs(tmp_path: Path):
    src = tmp_path / "in"
    src.mkdir()
    photo(300, 200).save(src / "a.png")
    photo(300, 200, gray=True).save(src / "b.jpg")
    (src / "broken.png").write_bytes(b"not an image")
    (src / "notes.txt").write_text("ignored")
    files = core.collect_files(src)
    assert [f.name for f in files] == ["a.png", "b.jpg", "broken.png"]

    s = core.Settings(grid_width=100, output_width=300, fmt="both")
    out = core.default_out_dir(src)
    seen = []
    results = core.run_batch(files, out, s, progress=lambda i, n, r: seen.append((i, n)))
    assert seen == [(1, 3), (2, 3), (3, 3)]
    ok = [r for r in results if not r.error]
    assert len(ok) == 2 and results[2].error
    assert {p.name for p in ok[0].outputs} == {"a_redsun_paint28.png", "a_redsun_paint28.bmp"}
    assert {p.name for p in ok[1].outputs} == {"b_redsun_bw.png", "b_redsun_bw.bmp"}
    with Image.open(out / "a_redsun_paint28.bmp") as bmp:
        assert bmp.mode == "P" and bmp.size == (300, 201)  # 67-row grid x 3
    with Image.open(out / "b_redsun_bw.png") as png:
        assert png.mode == "1"


def test_settings_validation():
    import pytest

    with pytest.raises(ValueError):
        core.Settings(palette="rainbow").validate()
    with pytest.raises(ValueError):
        core.Settings(grid_width=5).validate()
    assert core.Settings(grid_width=640, output_width=3200).factor() == 5
    assert core.Settings(grid_width=640, output_width=500).factor() == 1


def test_logo_red_survives_contrast_boost():
    """A mostly-white graphic must keep its red; a histogram stretch would turn it black."""
    img = Image.new("RGB", (400, 400), "white")
    for y in range(100, 300):
        for x in range(100, 300):
            img.putpixel((x, y), (200, 30, 30))
    s = core.Settings(mode="color", palette="paint", grid_width=200, output_width=200)
    assert (255, 0, 0) in rgb_colors(core.process(img, s).image)


def test_despeckle_removes_grain():
    s_on = core.Settings(mode="color", grid_width=200, output_width=200, despeckle=True)
    s_off = core.Settings(mode="color", grid_width=200, output_width=200, despeckle=False)
    noisy = photo(600, 400)

    def specks(img):
        px = img.load()
        w, h = img.size
        return sum(
            1 for y in range(1, h - 1) for x in range(1, w - 1)
            if px[x, y] not in (px[x - 1, y], px[x + 1, y], px[x, y - 1], px[x, y + 1])
        )

    assert specks(core.process(noisy, s_on).image) * 5 < specks(core.process(noisy, s_off).image)
