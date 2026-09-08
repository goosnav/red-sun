"""Native file/folder picker, run as a subprocess so Tk never touches the server thread.

Usage: python -m red_sun.pick file|folder [initial_dir]
Prints the chosen path (empty when cancelled). Exit 2 when Tk is unavailable.
"""

import os
import sys
from pathlib import Path

IMAGE_TYPES = [("Images", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp *.gif"), ("All files", "*")]


def main() -> int:
    kind = sys.argv[1] if len(sys.argv) > 1 else "file"
    initial = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
    # Inside a venv of a uv-managed (python-build-standalone) interpreter, Tcl looks for its
    # library relative to the venv and fails. Point it at the base interpreter's copy.
    for var, name in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        candidate = Path(sys.base_prefix) / "lib" / name
        if var not in os.environ and candidate.is_dir():
            os.environ[var] = str(candidate)
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001
        print(f"tkinter unavailable: {exc}", file=sys.stderr)
        return 2
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    opts = {"initialdir": initial} if initial else {}
    if kind == "folder":
        path = filedialog.askdirectory(title="Choose a folder of images", **opts)
    else:
        path = filedialog.askopenfilename(title="Choose an image", filetypes=IMAGE_TYPES, **opts)
    root.destroy()
    print(path or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
