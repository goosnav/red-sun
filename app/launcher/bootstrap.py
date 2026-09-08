#!/usr/bin/env python3
"""Run the manifest entry module after the native supervisor prepares runtime state."""

from __future__ import annotations

import faulthandler
import importlib
import json
import os
import runpy
import sys
import traceback
from pathlib import Path


def main() -> int:
    faulthandler.enable()
    app_root = Path(os.environ["GOOSNAV_APP_ROOT"]).resolve()
    manifest_path = app_root / "launcher" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry_module = manifest["entry_module"]
    os.chdir(app_root)
    sys.path.insert(0, str(app_root / "src"))
    if os.environ.get("GOOSNAV_VALIDATE_ONLY") == "1":
        importlib.import_module(entry_module)
        return 0
    try:
        runpy.run_module(entry_module, run_name="__main__", alter_sys=True)
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
