// Playback engine: fetches audio chunk by chunk from the server, prefetches
// the next two, and plays them in order through one <audio> element. It knows
// nothing about the page; the UI hooks in through the on*() callbacks.
import { audioErrorMessage, audioUrl } from './api.js';

export class Player {
  constructor() {
    this.audio    = new Audio();
    this.docId    = null;
    this.chunks   = [];
    this.idx      = 0;
    this.playing  = false;
    this.voice    = 'casual_male';
    this.speed    = 1.0;
    this._cache       = new Map();   // idx -> Promise<string|null>
    this._controllers = new Map();   // idx -> AbortController
    this._errors      = new Map();   // idx -> last error message
    this._genTimes    = new Map();   // idx -> generation ms
    this._rtfWindow   = [];          // rolling RTF samples (last 5)
    // Bumped on every action that supersedes a pending _playIdx (load, next,
    // prev, seekTo, stop, setVoice). An in-flight _playIdx captures the gen
    // at entry and bails out after its await if the gen has changed — this
    // is what guarantees strict 0→1→2 playback ordering across user actions.
    this._playGen     = 0;
    // Audio elements fire 'error' when src is cleared. We use this flag to
    // distinguish those from real decode failures so we don't treat them as
    // a reason to skip ahead a chunk.
    this._clearingSrc = false;

    this.audio.addEventListener('ended',      () => this._onEnded());
    this.audio.addEventListener('error',      () => this._onError());
    this.audio.addEventListener('waiting',    () => this.onLoading(true));
    this.audio.addEventListener('playing',    () => this.onLoading(false));
    this.audio.addEventListener('timeupdate', () => {
      this.onTimeUpdate(this.audio.currentTime, this.audio.duration || 0);
    });
    this.audio.addEventListener('loadedmetadata', () => {
      const dur   = this.audio.duration;
      const genMs = this._genTimes.get(this.idx);
      if (dur > 0 && genMs > 0) {
        const rtf = dur / (genMs / 1000);
        this._rtfWindow.push(rtf);
        if (this._rtfWindow.length > 5) this._rtfWindow.shift();
        const avg = this._rtfWindow.reduce((a, b) => a + b, 0) / this._rtfWindow.length;
        this.onSpeedUpdate(avg, genMs);
      }
    });
  }

  // Reset the <audio> element without firing a spurious 'error' event.
  _clearAudio() {
    this._clearingSrc = true;
    this.audio.pause();
    this.audio.removeAttribute('src');
    this.audio.load();
    this._clearingSrc = false;
  }

  load(docId, chunks) {
    this._playGen++;
    this._abortAll();      // don't let the previous document keep the TTS busy
    this._clearAudio();
    this.docId   = docId;
    this.chunks  = chunks;
    this.idx     = 0;
    this.playing = false;
    this._clearCache();
    if (chunks.length) this._fetchChunk(0);   // warm the first chunk
    this.onChunkChange(0);
    this.onStateChange();
  }

  async play() {
    this.playing = true;
    await this._playIdx(this.idx);
  }

  pause() {
    this.playing = false;
    this.audio.pause();
    this.onStateChange();
  }

  resume() {
    this.playing = true;
    this.audio.playbackRate = this.speed;
    this.audio.play().catch(() => this._playIdx(this.idx));
    this.onStateChange();
  }

  async next() {
    if (this.idx >= this.chunks.length - 1) return;
    this._playGen++;
    this.audio.pause();
    this.idx++;
    if (this.playing) {
      await this._playIdx(this.idx);
    } else {
      this._ensureChain(this.idx);
      this.onChunkChange(this.idx);
    }
  }

  async prev() {
    if (this.idx <= 0) return;
    this._playGen++;
    this.audio.pause();
    this.idx--;
    if (this.playing) {
      await this._playIdx(this.idx);
    } else {
      this._ensureChain(this.idx);
      this.onChunkChange(this.idx);
    }
  }

  async seekTo(idx) {
    this._playGen++;
    const was = this.playing;
    this._clearAudio();
    this.idx = idx;
    if (was) {
      this.playing = true;
      await this._playIdx(idx);
    } else {
      this._ensureChain(idx);
      this.onChunkChange(idx);
    }
  }

  // Ensure the sequential chain is seeded from idx if it isn't already running.
  _ensureChain(idx) {
    if (!this._cache.has(idx)) this._fetchChunk(idx);
  }

  setVoice(v) {
    if (v === this.voice) return;
    this.voice = v;
    this._playGen++;
    // Old-voice requests are useless now; cancel them so they don't queue
    // ahead of the new ones on the TTS server.
    this._abortAll();
    // Drop cached URLs for the old voice but DON'T revoke the URL the audio
    // element is currently playing — that would cut the chunk off mid-word
    // and trip _onError into skipping ahead.
    const playingSrc = this.audio.src;
    this._cache.forEach(async p => {
      const u = await p;
      if (u && u !== playingSrc) URL.revokeObjectURL(u);
    });
    this._cache.clear();
    this._errors.clear();
    if (!this.chunks.length) return;
    if (this.playing && (this.audio.paused || this.audio.ended || !this.audio.src)) {
      // A chunk was still being generated in the old voice when the voice
      // changed; the _playIdx waiting on it has bailed, so restart it.
      this._playIdx(this.idx);
    } else {
      // Current chunk keeps playing in the old voice; warm the next one.
      this._fetchChunk(Math.min(this.idx + 1, this.chunks.length - 1));
    }
  }
  setSpeed(s) { this.speed = s; this.audio.playbackRate = s; }

  // ── internals ──

  async _playIdx(idx) {
    // Capture the gen at entry. If anything supersedes us during the await
    // below (a next/prev/seek/stop/load), this snapshot lets us bail before
    // we clobber audio.src with stale playback. This is what enforces
    // strict 0 → 1 → 2 ordering: a concurrent _playIdx(2) bumps _playGen,
    // and the older _playIdx(1) sees the mismatch and exits.
    const myGen = this._playGen;

    if (idx >= this.chunks.length) {
      this.playing = false;
      this.onPlaybackComplete();
      return;
    }

    // Same rule as the server's is_speakable(): skip punctuation-only crumbs
    // but keep short sentences like "Yes." or "No.".
    const text = (this.chunks[idx].text || '').trim();
    if (!/[\p{L}\p{N}]{2}/u.test(text)) {
      if (this.playing && myGen === this._playGen) {
        this.idx = idx + 1;
        await this._playIdx(idx + 1);
      }
      return;
    }

    this.idx = idx;
    this.onChunkChange(idx);

    this.onLoading(true);
    const url = await this._fetchChunk(idx);
    this.onLoading(false);

    if (myGen !== this._playGen || !this.playing) return;

    if (!url) {
      // Generation failed even after retries: stop and say so instead of
      // silently skipping through the document.
      this.playing = false;
      this.onStateChange();
      this.onChunkError(idx, this._errors.get(idx) || 'Audio generation failed');
      return;
    }

    // Chunk idx is confirmed ready — pre-warm the next two while this one
    // plays. _fetchChunk dedupes via cache, so this is safe to call repeatedly.
    for (let n = 1; n <= 2; n++) {
      if (idx + n < this.chunks.length) this._fetchChunk(idx + n);
    }

    this.audio.src = url;
    this.audio.playbackRate = this.speed;
    try {
      await this.audio.play();
      this.onStateChange();
    } catch {
      this.playing = false;
      this.onStateChange();
    }
  }

  _fetchChunk(idx) {
    if (this._cache.has(idx)) return this._cache.get(idx);
    const ctrl = new AbortController();
    this._controllers.set(idx, ctrl);
    const p = this._fetchWithRetry(idx, ctrl.signal)
      .catch(e => {
        if (e.name !== 'AbortError') this._errors.set(idx, e.message);
        return null;
      })
      .then(url => {
        // Only remove our own controller: a newer request for the same idx
        // (after a voice change) may have replaced it, and Stop must still
        // be able to abort that one.
        if (this._controllers.get(idx) === ctrl) this._controllers.delete(idx);
        if (this._cache.get(idx) !== p) {
          // Superseded by load/stop/voice change while in flight.
          if (url) URL.revokeObjectURL(url);
          return null;
        }
        if (url) this.onChunkCached(idx);
        else this._cache.delete(idx);   // don't cache failures; allow a retry
        return url;
      });
    this._cache.set(idx, p);
    return p;
  }

  async _fetchWithRetry(idx, signal, maxRetries = 2) {
    // Capture these now: retries must not pick up a different doc or voice.
    const docId = this.docId;
    const url   = audioUrl(docId, idx, this.voice);
    const t0    = performance.now();
    let restored = false;
    let lastErr  = 'Audio generation failed';
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      let r;
      try {
        r = await fetch(url, { signal });
      } catch (e) {
        if (e.name === 'AbortError') throw e;   // intentional stop — don't retry
        lastErr = 'Reader server unreachable';
        if (attempt < maxRetries) await _sleep(600 * (attempt + 1), signal);
        continue;
      }
      if (r.ok) {
        const b = await r.blob();
        this._genTimes.set(idx, performance.now() - t0);
        return URL.createObjectURL(b);
      }
      lastErr = await audioErrorMessage(r);
      if (r.status === 404 && !restored) {
        // The server lost the document (e.g. it restarted): re-send it and
        // retry straight away without counting this as an attempt.
        restored = true;
        if (await this.onDocMissing(docId)) { attempt--; continue; }
      }
      if (r.status >= 400 && r.status < 500) break;   // retrying won't help
      if (attempt < maxRetries) await _sleep(600 * (attempt + 1), signal);
    }
    throw new Error(lastErr);
  }

  stop() {
    this._playGen++;
    this.playing = false;
    this._clearAudio();
    this._abortAll();
    this._clearCache();
    this.onStateChange();
  }

  _abortAll() {
    this._controllers.forEach(ctrl => ctrl.abort());
    this._controllers.clear();
  }

  _clearCache() {
    this._cache.forEach(async p => { const u = await p; if (u) URL.revokeObjectURL(u); });
    this._cache.clear();
    this._errors.clear();
  }

  async _onEnded() {
    if (this.playing) { this.idx++; await this._playIdx(this.idx); }
  }
  async _onError() {
    // Clearing audio.src dispatches an 'error' event — ignore those so we
    // don't mistake them for a decode failure and skip a chunk.
    if (this._clearingSrc) return;
    if (!this.audio.src) return;
    if (this.playing) { this.idx++; await this._playIdx(this.idx); }
  }

  // Callbacks (overridden by UI)
  onChunkChange(idx)             {}
  onStateChange()                {}
  onTimeUpdate(cur, dur)         {}
  onLoading(flag)                {}
  onPlaybackComplete()           {}
  onChunkCached(idx)             {}
  onSpeedUpdate(rtf, genMs)      {}
  onChunkError(idx, message)     {}
  async onDocMissing(docId)      { return false; }
}

// Resolves after ms, or rejects with an AbortError as soon as signal aborts.
function _sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      clearTimeout(t);
      reject(new DOMException('Aborted', 'AbortError'));
    }, { once: true });
  });
}
