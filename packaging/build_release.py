#!/usr/bin/env python3
"""Build the Red Sun release ZIP.

Steps: icons -> pinned uv tools -> supervisor tests -> launcher images this host can build
-> staged release root -> ZIP. Prints exactly which images were built and why any were skipped,
so a release never claims a platform that was not built here.

Run from the repository root:  python3 packaging/build_release.py
Options: --skip-fetch (reuse downloaded uv tools), --targets macos windows-x64 ... (subset)
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"
LAUNCHER = PKG / "launcher"
APP = ROOT / "app"
NAME = "Red Sun"
BUNDLE_ID = "com.goosnav.red-sun"
UV_VERSION = "0.11.28"
ALL_TARGETS = ("macos", "windows-x64", "windows-arm64", "linux-x64", "linux-arm64")
IMAGE_NAMES = {
    "macos": f"Open {NAME} — macOS.app",
    "windows-x64": f"Open {NAME} — Windows x64.exe",
    "windows-arm64": f"Open {NAME} — Windows ARM64.exe",
    "linux-x64": f"Open {NAME} — Linux x86_64.AppImage",
    "linux-arm64": f"Open {NAME} — Linux ARM64.AppImage",
}


def run(*args: object, cwd: Path | None = None) -> None:
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], cwd=cwd, check=True)


def build_image(target: str, stage: Path, extra: list[object]) -> None:
    run(sys.executable, LAUNCHER / "build.py", "--target", target, "--name", NAME, "--bundle-id", BUNDLE_ID,
        "--icons", PKG / "icons", "--output", stage, *extra)


def windows_resource(arch: str, work: Path) -> Path | None:
    """Compile the icon into a .syso with go-winres (go install github.com/tc-hib/go-winres@latest)."""
    tool = shutil.which("go-winres") or (Path.home() / "go" / "bin" / "go-winres")
    if not Path(tool).exists():
        return None
    work.mkdir(parents=True, exist_ok=True)
    run(tool, "simply", "--icon", PKG / "icons" / "AppIcon.ico", "--arch", arch, "--manifest", "gui",
        "--product-name", NAME, "--out", work / "resource")
    return work / f"resource_windows_{arch}.syso"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(path: Path) -> str:
    """Stable digest of a directory (macOS .app) so image stability can be checked across releases."""
    digest = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def partial_zip(stage: Path, output: Path) -> None:
    """Same layout as the official ZIP, minus the images that could not be built here."""
    top = f"{NAME}-M1a"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in stage.rglob("*") if p.is_file()):
            info = zipfile.ZipInfo.from_file(path, (Path(top) / path.relative_to(stage)).as_posix())
            info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
            with path.open("rb") as src, archive.open(info, "w") as dst:
                shutil.copyfileobj(src, dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=ROOT / "dist")
    ap.add_argument("--skip-fetch", action="store_true", help="reuse already-downloaded uv tools")
    ap.add_argument("--targets", nargs="*", choices=ALL_TARGETS, default=list(ALL_TARGETS))
    a = ap.parse_args()
    dist: Path = a.output
    stage = dist / f"{NAME}-M1a"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    run(sys.executable, PKG / "make_icon.py")
    if not a.skip_fetch:
        run(sys.executable, LAUNCHER / "fetch_uv.py", "--version", UV_VERSION, "--app-root", APP)
    run("go", "test", "./...", cwd=LAUNCHER / "supervisor")

    built: list[str] = []
    skipped: list[tuple[str, str]] = []
    for target in a.targets:
        if target == "macos":
            if platform.system() != "Darwin":
                skipped.append((target, "macOS host required for lipo and iconutil"))
                continue
            build_image(target, stage, [])
        elif target.startswith("windows"):
            arch = "amd64" if target == "windows-x64" else "arm64"
            syso = windows_resource(arch, dist / "winres")
            if syso is None:
                skipped.append((target, "go-winres not installed: go install github.com/tc-hib/go-winres@latest"))
                continue
            build_image(target, stage, ["--windows-resource", syso])
        else:
            if not shutil.which("appimagetool"):
                skipped.append((target, "appimagetool not on PATH (build on a Linux host of that architecture)"))
                continue
            build_image(target, stage, [])
        built.append(target)

    shutil.copytree(APP, stage / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", ".pytest_cache", ".keep"))
    for tools_dir in (stage / "app" / "launcher" / "tools").iterdir():
        (tools_dir / ".keep").unlink(missing_ok=True)
    shutil.copy2(PKG / "release" / "README.txt", stage / "README.txt")
    shutil.copy2(ROOT / "LICENSE", stage / "LICENSE.txt")

    complete = len(built) == len(ALL_TARGETS)
    if complete:
        output = dist / f"{NAME}-M1a.zip"
        run(sys.executable, LAUNCHER / "package_universal.py", "--root", stage, "--name", NAME, "--output", output)
    else:
        output = dist / f"{NAME}-M1a-PARTIAL-{'+'.join(built) or 'no-images'}.zip"
        partial_zip(stage, output)

    print("\nLauncher images built:")
    for target in built:
        image = stage / IMAGE_NAMES[target]
        print(f"  {IMAGE_NAMES[target]}  sha256={tree_hash(image) if image.is_dir() else sha256(image)}")
    for target, reason in skipped:
        print(f"  SKIPPED {target}: {reason}")
    print(f"\n{'Universal' if complete else 'PARTIAL'} ZIP: {output}  ({output.stat().st_size // 1_000_000} MB)")
    if not complete:
        print("This ZIP only supports the platforms listed above. Do not publish it as the universal release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
