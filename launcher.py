"""
Launcher de escritorio para Chat Document AI.

Inicia el backend local, abre la interfaz web compilada y permite
empaquetar todo en un .exe con PyInstaller.
"""

from __future__ import annotations

import sys
import threading
import time
import webbrowser
import os
import subprocess
import shutil
import urllib.request
from pathlib import Path

import uvicorn


BASE_DIR = Path(__file__).resolve().parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.app import app  # noqa: E402
from backend.config import HOST, PORT  # noqa: E402


def run_server() -> None:
    """Ejecuta el servidor FastAPI en segundo plano."""
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )


def is_app_ready(url: str) -> bool:
    """Comprueba si la app ya está respondiendo en el puerto local."""
    try:
        with urllib.request.urlopen(url, timeout=1):
            return True
    except Exception:
        return False


def open_app_url(url: str) -> bool:
    """Intenta abrir la URL en un navegador real con fallbacks robustos."""
    try:
        if webbrowser.open(url, new=2):
            return True
    except Exception:
        pass

    browser_candidates = [
        "msedge.exe",
        "chrome.exe",
        "firefox.exe",
        "brave.exe",
    ]

    for browser in browser_candidates:
        browser_path = shutil.which(browser)
        if not browser_path:
            continue
        try:
            subprocess.Popen([browser_path, url], shell=False)
            return True
        except Exception:
            continue

    try:
        os.startfile(url)
        return True
    except Exception:
        return False


def main() -> None:
    url = f"http://127.0.0.1:{PORT}"

    if is_app_ready(url):
        open_app_url(url)
        return

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    for _ in range(30):
        if is_app_ready(url):
            break
        if not server_thread.is_alive():
            break
        time.sleep(0.5)

    open_app_url(url)

    try:
        while server_thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()