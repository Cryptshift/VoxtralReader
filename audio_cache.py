"""
On-disk cache of generated audio, so paragraphs that were already read are
served instantly without asking the TTS server again.

- Keyed by a hash of everything that determines the audio: model id, voice,
  the generation parameters sent to the TTS server and the exact text.
- Writes are atomic (temp file + rename), so an interrupted or cancelled
  request can never leave a truncated file behind.
- Size-capped with least-recently-used eviction (a cache hit refreshes the
  file's modification time).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

_SCHEMA = 1           # bump if the meaning of cached files changes
_STALE_TMP_S = 3600   # temp files older than this are leftovers from a crash

_lock = threading.Lock()


def cache_key(*, model: str, voice: str, params: dict, text: str) -> str:
    blob = json.dumps(
        {"v": _SCHEMA, "model": model, "voice": voice, "params": params, "text": text},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class AudioCache:
    def __init__(self, root: Path, max_bytes: int):
        self.root = Path(root)
        self.max_bytes = max(0, int(max_bytes))

    @property
    def enabled(self) -> bool:
        return self.max_bytes > 0

    def _path(self, key: str) -> Path:
        if len(key) != 64 or not all(c in "0123456789abcdef" for c in key):
            raise ValueError("invalid cache key")
        return self.root / key[:2] / f"{key}.wav"

    def get(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        path = self._path(key)
        try:
            data = path.read_bytes()
        except OSError:
            return None
        try:
            os.utime(path)          # mark as recently used
        except OSError:
            pass
        return data

    def put(self, key: str, data: bytes) -> None:
        if not self.enabled or len(data) > self.max_bytes:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{key}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)   # atomic: readers see all or nothing
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        self.evict()

    def _entries(self) -> list[tuple[float, int, Path]]:
        entries = []
        if not self.root.exists():
            return entries
        now = time.time()
        for path in self.root.glob("*/*"):
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            if path.suffix == ".tmp":
                if now - st.st_mtime > _STALE_TMP_S:
                    path.unlink(missing_ok=True)
                continue
            if path.suffix == ".wav":
                entries.append((st.st_mtime, st.st_size, path))
        return entries

    def evict(self) -> None:
        """Delete least-recently-used files until the cache fits its cap."""
        with _lock:
            entries = self._entries()
            total = sum(size for _, size, _ in entries)
            if total <= self.max_bytes:
                return
            for _, size, path in sorted(entries):
                path.unlink(missing_ok=True)
                total -= size
                if total <= self.max_bytes:
                    break

    def stats(self) -> dict:
        entries = self._entries()
        return {
            "bytes": sum(size for _, size, _ in entries),
            "files": len(entries),
            "max_bytes": self.max_bytes,
        }

    def clear(self) -> None:
        with _lock:
            if not self.root.exists():
                return
            for path in self.root.glob("*/*"):
                if path.suffix in (".wav", ".tmp"):
                    path.unlink(missing_ok=True)
            for d in self.root.iterdir():
                if d.is_dir():
                    try:
                        d.rmdir()
                    except OSError:
                        pass
