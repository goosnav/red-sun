"""GUI entry point (the launcher manifest's entry_module, and what run.sh starts).

Binds loopback, publishes the effective URL for the native launcher when one is
present, otherwise opens the default browser itself, then serves until Quit.
"""

from __future__ import annotations

import json
import os
import webbrowser
from pathlib import Path

from . import __version__
from .server import make_server


def main() -> int:
    host = os.environ.get("GOOSNAV_HOST", "127.0.0.1")
    preferred = int(os.environ.get("GOOSNAV_PORT_PREFERENCE") or os.environ.get("RED_SUN_PORT") or 0)
    try:
        server = make_server(host, preferred)
    except OSError:
        server = make_server(host, 0)  # preferred port taken: let the OS choose, never probe and race
    url = f"http://{host}:{server.server_address[1]}"

    state = os.environ.get("GOOSNAV_RUNTIME_STATE")
    if state:
        target = Path(state)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps({"schema_version": 1, "pid": os.getpid(), "url": url}), encoding="utf-8")
        os.replace(tmp, target)
    elif not os.environ.get("RED_SUN_NO_BROWSER"):
        webbrowser.open(url)

    print(f"Red Sun {__version__} is running at {url}  (Quit from the page, or press Ctrl+C)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
