import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import app as app_module
import audio_cache
from audio_cache import AudioCache, cache_key

BASE = dict(model="m", voice="casual_male", params={"response_format": "pcm", "stream": True},
            text="Hello there.")


# ── Keying ───────────────────────────────────────────────────────────────────

def test_same_inputs_same_key_and_param_order_irrelevant():
    a = cache_key(**BASE)
    b = cache_key(**{**BASE, "params": {"stream": True, "response_format": "pcm"}})
    assert a == b and len(a) == 64


@pytest.mark.parametrize("change", [
    {"model": "other-model"},
    {"voice": "fr_female"},
    {"params": {"response_format": "pcm", "stream": True, "speed": 1.25}},
    {"text": "Hello there!"},
    {"text": "Hello  there."},       # exact text, whitespace included
])
def test_any_difference_changes_the_key(change):
    assert cache_key(**{**BASE, **change}) != cache_key(**BASE)


# ── Storage, LRU eviction, atomic writes ─────────────────────────────────────

def key(n):
    return cache_key(**{**BASE, "text": f"text {n}"})


def test_roundtrip_and_stats(tmp_path):
    c = AudioCache(tmp_path, 10_000)
    assert c.get(key(1)) is None
    c.put(key(1), b"RIFFdata")
    assert c.get(key(1)) == b"RIFFdata"
    assert c.stats() == {"bytes": 8, "files": 1, "max_bytes": 10_000}
    c.clear()
    assert c.stats()["files"] == 0 and c.get(key(1)) is None


def test_disabled_cache_stores_nothing(tmp_path):
    c = AudioCache(tmp_path, 0)
    c.put(key(1), b"x" * 10)
    assert c.get(key(1)) is None
    assert not list(tmp_path.rglob("*.wav"))


def _age(c, k, seconds_ago):
    t = time.time() - seconds_ago
    os.utime(c._path(k), (t, t))


def test_evicts_least_recently_used(tmp_path):
    c = AudioCache(tmp_path, 1000)
    for n in (1, 2):
        c.put(key(n), b"x" * 400)
    _age(c, key(1), 300)
    _age(c, key(2), 200)
    c.get(key(1))                       # a hit makes 1 the most recently used
    c.put(key(3), b"x" * 400)           # 1200 bytes > 1000: evict the LRU
    assert c.get(key(2)) is None
    assert c.get(key(1)) is not None and c.get(key(3)) is not None
    assert c.stats()["bytes"] <= 1000


def test_item_larger_than_cap_is_not_cached(tmp_path):
    c = AudioCache(tmp_path, 100)
    c.put(key(1), b"x" * 101)
    assert c.get(key(1)) is None


def test_failed_write_leaves_no_partial_file(tmp_path, monkeypatch):
    c = AudioCache(tmp_path, 10_000)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(audio_cache.os, "replace", boom)
    with pytest.raises(OSError):
        c.put(key(1), b"RIFF" + b"x" * 100)
    assert c.get(key(1)) is None
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


def test_stale_temp_files_are_cleaned_and_not_counted(tmp_path):
    c = AudioCache(tmp_path, 10_000)
    c.put(key(1), b"abc")
    stale = c._path(key(1)).with_name(".leftover.tmp")
    stale.write_bytes(b"partial")
    old = time.time() - 2 * audio_cache._STALE_TMP_S
    os.utime(stale, (old, old))
    assert c.stats() == {"bytes": 3, "files": 1, "max_bytes": 10_000}
    assert not stale.exists()


def test_rejects_malformed_keys(tmp_path):
    with pytest.raises(ValueError):
        AudioCache(tmp_path, 100).get("../../etc/passwd")


# ── /audio with a fake TTS server ────────────────────────────────────────────

PCM = b"\x01\x00\x02\x00" * 1000


@pytest.fixture
def fake_tts(tmp_path, monkeypatch):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(200, content=PCM)

    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_module, "HIST_FILE", tmp_path / "history.json")
    monkeypatch.setattr(app_module, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(app_module, "_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    app_module.documents.clear()
    app_module.documents["d"] = {"chunks": [{"text": "Hello there."}, {"text": "Second one."}],
                                 "total_chunks": 2}
    with TestClient(app_module.app) as client:
        yield client, calls


def test_second_request_is_served_from_cache(fake_tts):
    client, calls = fake_tts
    first = client.get("/audio/d/0?voice=casual_male")
    second = client.get("/audio/d/0?voice=casual_male")
    assert first.status_code == second.status_code == 200
    assert first.headers["x-cache"] == "miss" and second.headers["x-cache"] == "hit"
    assert first.content == second.content and first.content[:4] == b"RIFF"
    assert len(calls) == 1
    # A different voice is a different cache entry.
    assert client.get("/audio/d/0?voice=fr_female").headers["x-cache"] == "miss"
    assert len(calls) == 2


def test_cache_endpoints(fake_tts):
    client, _ = fake_tts
    client.get("/audio/d/0")
    client.get("/audio/d/1")
    stats = client.get("/cache").json()
    assert stats["files"] == 2 and stats["bytes"] > 0
    assert client.delete("/cache").json()["files"] == 0
    assert client.get("/audio/d/0").headers["x-cache"] == "miss"


def test_cancelled_request_is_not_cached(fake_tts, monkeypatch):
    client, _ = fake_tts

    async def gone(request, coro):
        coro.close()
        raise app_module._ClientGone

    monkeypatch.setattr(app_module, "_unless_disconnected", gone)
    assert client.get("/audio/d/0").status_code == 499
    assert client.get("/cache").json()["files"] == 0


def test_tts_error_is_not_cached(fake_tts, monkeypatch):
    client, _ = fake_tts
    monkeypatch.setattr(app_module, "_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))))
    assert client.get("/audio/d/0").status_code == 502
    assert client.get("/cache").json()["files"] == 0
