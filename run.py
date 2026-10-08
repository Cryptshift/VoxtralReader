#!/usr/bin/env python3
"""Voxtral Reader — desktop launcher (frameless, always-on-top window)."""
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
ROOT     = Path(__file__).resolve().parent
TTS_PORT = int(os.getenv("TTS_PORT", 8000))
APP_PORT = int(os.getenv("APP_PORT", 7860))
TTS_URL  = f"http://127.0.0.1:{TTS_PORT}"
APP_URL  = f"http://127.0.0.1:{APP_PORT}"
LOG_DIR  = ROOT / "logs"
TTS_LOG  = LOG_DIR / "tts.log"

# app.py reads these at import time (uvicorn imports it in this process).
os.environ["TTS_URL"] = TTS_URL

_tts_proc: subprocess.Popen | None = None


# ── Server helpers ─────────────────────────────────────────────────────────────

def _probe(url: str, timeout: float = 2.0) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def _port_owner(port: int) -> str:
    """'Python (pid 123)' for whatever listens on port, or '' if it's free."""
    try:
        out = subprocess.run(
            ["lsof", "+c", "0", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return ""
    owners = {f"{cols[0]} (pid {cols[1]})" for cols in
              (line.split() for line in out.splitlines()[1:]) if len(cols) > 1}
    return ", ".join(sorted(owners))


def _log_tail(n: int = 15) -> str:
    try:
        lines = TTS_LOG.read_text(errors="replace").splitlines()[-n:]
    except OSError:
        return "    (no log)"
    return "\n".join("    " + line for line in lines)


def _start_tts() -> None:
    global _tts_proc
    if _probe(f"{TTS_URL}/v1/models"):
        print("  ✓ mlx-audio already running")
        return
    owner = _port_owner(TTS_PORT)
    if owner:
        print(f"  ✗ Port {TTS_PORT} is in use by {owner}, which isn't an mlx-audio "
              f"server. Stop it or set TTS_PORT. Reading aloud won't work.")
        return

    LOG_DIR.mkdir(exist_ok=True)
    print(f"  → Starting mlx-audio TTS server (log: {TTS_LOG.relative_to(ROOT)})")
    log = open(TTS_LOG, "ab")
    log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} run.py ===\n".encode())
    log.flush()
    _tts_proc = subprocess.Popen(
        [sys.executable, "-m", "mlx_audio.server",
         "--host", "127.0.0.1", "--port", str(TTS_PORT)],
        stdout=log, stderr=subprocess.STDOUT, cwd=ROOT,
    )
    log.close()  # the child keeps its own handle

    for _ in range(90):
        time.sleep(2)
        if _tts_proc.poll() is not None:
            print(f"  ✗ TTS server exited (code {_tts_proc.returncode}). "
                  f"Last lines of {TTS_LOG}:\n{_log_tail()}")
            return
        if _probe(f"{TTS_URL}/v1/models"):
            print("  ✓ TTS server ready"); return
    print(f"  ✗ TTS server didn't answer after 3 minutes; see {TTS_LOG}")


def _stop_tts() -> None:
    if _tts_proc is None or _tts_proc.poll() is not None:
        return
    _tts_proc.terminate()
    try:
        _tts_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _tts_proc.kill()


def _start_app() -> None:
    os.chdir(ROOT)
    uvicorn.run("app:app", host="127.0.0.1", port=APP_PORT, log_level="warning")


def _wait_for_app(timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _probe(f"{APP_URL}/health", timeout=1.0):
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
    print("\n  ▶ Voxtral Reader\n  " + "─" * 33)

    owner = _port_owner(APP_PORT)
    if owner and not _probe(f"{APP_URL}/health", timeout=1.0):
        print(f"  ✗ Port {APP_PORT} is in use by {owner}. "
              f"Stop it or set APP_PORT to another port.")
        sys.exit(1)

    threading.Thread(target=_start_tts, daemon=True).start()
    if owner:
        print("  ✓ Using the Voxtral Reader server that is already running")
    else:
        threading.Thread(target=_start_app, daemon=True).start()

    print("  → Waiting for app server…")
    if not _wait_for_app():
        print("  ✗ App server failed to start"); _stop_tts(); sys.exit(1)
    print("  ✓ App ready")

    import app as _app
    _app._highlight_api = HighlightApi()

    win_api = WindowApi()
    window  = webview.create_window(
        title            = "Voxtral Reader",
        url              = APP_URL,
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

    try:
        webview.start(func=on_start, debug=False)
    finally:
        _stop_tts()


if __name__ == "__main__":
    main()
