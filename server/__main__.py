"""python -m server  ->  starts the API + web UI on http://127.0.0.1:8000 and opens a browser."""
from __future__ import annotations

import argparse
import threading
import time
import urllib.request
import webbrowser

import uvicorn


def _open_when_ready(url: str) -> None:
    for _ in range(120):  # up to ~30 s
        try:
            with urllib.request.urlopen(url + "api/health", timeout=1):
                break
        except OSError:
            time.sleep(0.25)
    print(f"\n  Dengue Watch is running at {url}\n  Press Ctrl+C to stop.\n", flush=True)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a missing browser must never stop the server
        pass


def main() -> None:
    p = argparse.ArgumentParser(description="Run the Dengue Watch web app.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    a = p.parse_args()
    url = f"http://{a.host}:{a.port}/"
    print(f"  Starting Dengue Watch at {url} (loading models...)", flush=True)
    if not a.no_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run("server.api:app", host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
