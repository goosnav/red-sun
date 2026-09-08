#!/usr/bin/env python3
"""Generate the Red Sun icon set: a pixel-art sun (red core, orange ring, yellow ring) on black.

Writes packaging/icons/AppIcon.{png,ico,icns} and app/static/{icon.png,favicon.ico}.
.icns needs macOS `iconutil`; it is skipped elsewhere with a notice.
Run: python3 packaging/make_icon.py
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "packaging" / "icons"
STATIC = ROOT / "app" / "static"

BLACK, RED, ORANGE, YELLOW = (0, 0, 0, 255), (255, 0, 0, 255), (255, 128, 0, 255), (255, 255, 0, 255)
GRID = 64  # master pixel grid; larger sizes are nearest-neighbour multiples, so it stays chunky


def render(n: int) -> Image.Image:
    """Draw the sun directly on an n x n grid with a pixel-rounded square corner."""
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    px = img.load()
    c = (n - 1) / 2
    red, orange, yellow = 0.20 * n, 0.31 * n, 0.42 * n
    corner = 0.18 * n
    for y in range(n):
        for x in range(n):
            # rounded square mask so the Dock/taskbar tile is not a hard black square
            dx = max(corner - x, x - (n - 1 - corner), 0)
            dy = max(corner - y, y - (n - 1 - corner), 0)
            if dx * dx + dy * dy > corner * corner:
                continue
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            px[x, y] = RED if d <= red else ORANGE if d <= orange else YELLOW if d <= yellow else BLACK
    return img


def sized(n: int) -> Image.Image:
    if n >= GRID and n % GRID == 0:
        return render(GRID).resize((n, n), Image.Resampling.NEAREST)
    return render(n)


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    STATIC.mkdir(parents=True, exist_ok=True)
    master = sized(1024)
    master.save(ICONS / "AppIcon.png")
    sized(256).save(STATIC / "icon.png")

    # Pillow keeps only sizes <= the base frame, so the base must be the largest.
    ico_sizes = [256, 128, 64, 48, 32, 16]
    frames = [sized(n) for n in ico_sizes]
    frames[0].save(ICONS / "AppIcon.ico", sizes=[(n, n) for n in ico_sizes], append_images=frames[1:])
    favicon = [sized(n) for n in (48, 32, 16)]
    favicon[0].save(STATIC / "favicon.ico", sizes=[(48, 48), (32, 32), (16, 16)], append_images=favicon[1:])

    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "AppIcon.iconset"
            iconset.mkdir()
            for n in (16, 32, 128, 256, 512):
                sized(n).save(iconset / f"icon_{n}x{n}.png")
                sized(n * 2).save(iconset / f"icon_{n}x{n}@2x.png")
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ICONS / "AppIcon.icns")], check=True)
    else:
        print("iconutil not found: AppIcon.icns skipped (macOS only)")
    for p in sorted(ICONS.iterdir()) + [STATIC / "icon.png", STATIC / "favicon.ico"]:
        print(f"{p.relative_to(ROOT)}  {p.stat().st_size} bytes")


if __name__ == "__main__":
    main()
