// Entry point: connects the player to the page and wires up the controls.
import * as api from './api.js';
import { exitBooksMode, initBooks, isBooksMode, nextPage } from './books.js';
import { initCacheControls, refreshCacheStats, scheduleCacheRefresh } from './cache.js';
import { addToHistory, loadHistory } from './library.js';
import { clearCachedMarks, markCached, renderDocument, showChunk, showPlayState } from './reader.js';
import { player, state } from './state.js';
import {
  $, initTabs, initWindowControls, setChunkProgress, setFileName, setLoading,
  setRealTimeFactor, setServerStatus, showToast,
} from './ui.js';

// ── Player → page ────────────────────────────────────────────────────────────
player.onChunkCached = idx => {
  markCached(idx);
  scheduleCacheRefresh();
};
player.onChunkChange = idx => showChunk(idx);
player.onStateChange = () => showPlayState();
player.onLoading     = flag => setLoading(flag);
player.onTimeUpdate  = (cur, dur) => setChunkProgress(cur, dur);
player.onSpeedUpdate = (rtf, genMs) => setRealTimeFactor(rtf, genMs);

player.onChunkError = (idx, message) => {
  showToast(`Couldn't generate paragraph ${idx + 1}: ${message}`, 7000, true);
};

// The server forgot the document (it restarted): re-register what we have.
player.onDocMissing = async docId => {
  if (!state.docData || state.docData.doc_id !== docId) return false;
  try {
    return await api.restoreDocument(state.docData);
  } catch {
    return false;
  }
};

player.onPlaybackComplete = () => {
  player.onStateChange();
  if (isBooksMode()) {
    nextPage();
  } else {
    showToast('Finished reading', 2500);
  }
};

// ── Transport controls ───────────────────────────────────────────────────────
const btnPlay = $('btn-play-pause');

btnPlay.addEventListener('click', () => {
  if (!state.docData) return;
  player.playing ? player.pause()
    : (player.audio.paused && player.audio.src ? player.resume() : player.play());
});

$('btn-stop').addEventListener('click', () => {
  player.stop();
  clearCachedMarks();
  showToast('Generation stopped', 2000);
});
$('btn-prev').addEventListener('click', () => player.prev());
$('btn-next').addEventListener('click', () => player.next());

const voiceSelect = $('voice-select');
voiceSelect.addEventListener('change', () => {
  player.setVoice(voiceSelect.value);
  clearCachedMarks();
});
$('speed-select').addEventListener('change', e => player.setSpeed(parseFloat(e.target.value)));

// Click on the progress bar to seek.
const progTrack = $('progress-track');
progTrack.addEventListener('click', e => {
  if (!state.docData || !player.chunks.length) return;
  const rect = progTrack.getBoundingClientRect();
  const pct  = (e.clientX - rect.left) / rect.width;
  const idx  = Math.round(pct * (player.chunks.length - 1));
  player.seekTo(Math.max(0, Math.min(idx, player.chunks.length - 1)));
});

document.addEventListener('keydown', e => {
  if (!state.docData) return;
  if (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT') return;
  if (e.code === 'Space') { e.preventDefault(); btnPlay.click(); }
  if (e.code === 'ArrowRight') player.next();
  if (e.code === 'ArrowLeft')  player.prev();
});

// ── Opening a PDF ────────────────────────────────────────────────────────────
$('file-input').addEventListener('change', async e => {
  const file = e.target.files[0];
  if (!file) return;

  setFileName('Loading…');
  showToast('Processing PDF…');

  try {
    const doc = await api.uploadPdf(file, $('include-refs').checked);
    state.docData = doc;
    exitBooksMode();
    setFileName(file.name);
    renderDocument(doc);
    addToHistory({
      doc_id:       doc.doc_id,
      filename:     file.name,
      timestamp:    new Date(),
      total_chunks: doc.total_chunks,
      data:         doc,
    });
    showToast(`Loaded ${doc.total_chunks} paragraphs`, 2500);
  } catch (err) {
    showToast(err.message, 7000, true);
    setFileName('Load failed');
  }

  e.target.value = '';
});

// ── Server status and voices ─────────────────────────────────────────────────
async function checkServerStatus() {
  try {
    setServerStatus(await api.getHealth());
  } catch {
    setServerStatus(null);
  }
}

async function loadVoices() {
  try {
    const j = await api.getVoices();
    const voices = (j.voices || []).filter(v => typeof v === 'string');
    if (!voices.length) return;
    const def = voices.includes(j.default) ? j.default
              : voices.includes(player.voice) ? player.voice : voices[0];
    voiceSelect.replaceChildren(...voices.map(v => new Option(v, v, false, v === def)));
    player.setVoice(def);
  } catch {}
}

// ── Init ─────────────────────────────────────────────────────────────────────
initTabs();
initWindowControls();
initBooks();
initCacheControls();

setInterval(checkServerStatus, 15000);
loadVoices();
loadHistory();
checkServerStatus();
refreshCacheStats();
