#!/usr/bin/env python3
"""Voxtral Reader — overlay launcher."""
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
import webview

# ── Config ────────────────────────────────────────────────────────────────────
TTS_PORT = int(os.getenv("TTS_PORT", 8000))
APP_PORT = int(os.getenv("APP_PORT", 7860))
MODEL    = os.getenv("TTS_MODEL", "mlx-community/Voxtral-4B-TTS-2603-mlx-bf16")
WORKERS  = int(os.getenv("WORKERS", 8))

_tts_proc: subprocess.Popen | None = None


# ── Server helpers ─────────────────────────────────────────────────────────────

def _probe(url: str, timeout: float = 2.0) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def _start_tts() -> None:
    global _tts_proc
    if _probe(f"http://localhost:{TTS_PORT}/v1/models"):
        print("  \u2713 mlx-audio already running")
        return
    print("  \u2192 Starting mlx-audio TTS server\u2026")
    _tts_proc = subprocess.Popen(
        [sys.executable, "-m", "mlx_audio.server",
         "--host", "127.0.0.1", "--port", str(TTS_PORT),
         "--workers", str(WORKERS)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(90):
        time.sleep(2)
        if _probe(f"http://localhost:{TTS_PORT}/v1/models"):
            print("  \u2713 TTS server ready"); return
    print("  \u2717 TTS server timed out \u2014 continuing anyway")


def _start_app() -> None:
    os.chdir(Path(__file__).parent)
    uvicorn.run("app:app", host="127.0.0.1", port=APP_PORT, log_level="warning")


def _wait_for_app(timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _probe(f"http://localhost:{APP_PORT}/health", timeout=1.0):
            return True
        time.sleep(0.25)
    return False


# ── Highlight API (stub — AppKit overlay wired up in on_start) ─────────────────

class HighlightApi:
    def set_rects(self, rects: list) -> None:
        pass
    def clear(self) -> None:
        pass


# ── Reader window API ──────────────────────────────────────────────────────────

class WindowApi:
    def __init__(self) -> None:
        self._win: webview.Window | None = None

    def close(self) -> None:
        if self._win: self._win.destroy()

    def minimize(self) -> None:
        if self._win: self._win.minimize()

    def set_on_top(self, value: bool) -> None:
        if self._win: self._win.on_top = value


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n  \u25b6 Voxtral Reader\n  " + "\u2500" * 33)

    threading.Thread(target=_start_tts, daemon=True).start()
    threading.Thread(target=_start_app, daemon=True).start()

    print("  \u2192 Waiting for app server\u2026")
    if not _wait_for_app():
        print("  \u2717 App server failed to start"); sys.exit(1)
    print("  \u2713 App ready")

    import app as _app
    _app._highlight_api = HighlightApi()

    win_api = WindowApi()
    window  = webview.create_window(
        title            = "Voxtral Reader",
        url              = f"http://localhost:{APP_PORT}",
        js_api           = win_api,
        width            = 1020,
        height           = 720,
        min_size         = (680, 480),
        frameless        = True,
        on_top           = True,
        resizable        = True,
        background_color = "#0d1117",
    )
    win_api._win = window

    def on_start():
        try:
            from AppKit import NSApp
            NSApp.activateIgnoringOtherApps_(True)
        except Exception as e:
            print(f"  ! activate: {e}")

    webview.start(func=on_start, debug=False)

    if _tts_proc is not None:
        _tts_proc.terminate()


if __name__ == "__main__":
    main()
