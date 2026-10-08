// The document view: paragraphs in the reading pane, the sections list, and
// which paragraph is active/playing/cached.
import { player, state } from './state.js';
import { $, $$, setFileName, setPlayIcon, setTransportEnabled, updateProgress } from './ui.js';

let chunkEls = [];

const NO_SECTIONS =
  '<div style="padding:20px 14px;color:var(--muted);font-size:12px;">Open a PDF to see sections</div>';

function progressFor(idx) {
  const chunk = player.chunks[idx];
  updateProgress(idx, player.chunks.length, chunk ? (chunk.section || '') : undefined);
}

export function renderDocument(data) {
  const chunksWrap  = $('chunks-container');
  const sectionList = $('section-list');
  $('empty-state').style.display = 'none';
  chunksWrap.innerHTML  = '';
  sectionList.innerHTML = '';
  chunkEls = [];

  let lastSection = null;
  data.chunks.forEach((chunk, i) => {
    if (chunk.section !== lastSection) {
      lastSection = chunk.section;
      const hdr = document.createElement('div');
      hdr.className   = 'section-header';
      hdr.textContent = chunk.section;
      hdr.dataset.section = chunk.section;
      chunksWrap.appendChild(hdr);
    }

    const el = document.createElement('div');
    el.className   = 'chunk';
    el.textContent = chunk.text;
    el.dataset.idx = i;
    el.addEventListener('click', () => player.seekTo(i));
    chunksWrap.appendChild(el);
    chunkEls.push(el);
  });

  data.sections.forEach(sec => {
    const item = document.createElement('div');
    item.className   = 'section-item';
    item.textContent = sec.name;
    item.dataset.chunkIdx = sec.chunk_idx;
    item.addEventListener('click', () => player.seekTo(sec.chunk_idx));
    sectionList.appendChild(item);
  });

  player.load(data.doc_id, data.chunks);
  setTransportEnabled(true);
  const first = player.chunks[0];
  updateProgress(0, data.total_chunks, first ? (first.section || '') : undefined);
}

// Close the open document (e.g. it was deleted from history).
export function clearDocument() {
  player.stop();   // also cancels any in-flight generation
  state.docData = null;
  $('chunks-container').innerHTML = '';
  $('section-list').innerHTML = NO_SECTIONS;
  $('empty-state').style.display = '';
  setTransportEnabled(false);
  setFileName('No file loaded');
}

// ── Reacting to the player ───────────────────────────────────────────────────

function markCurrent(idx, playing) {
  chunkEls.forEach((el, i) => {
    el.classList.toggle('active',  i === idx && !playing);
    el.classList.toggle('playing', i === idx &&  playing);
  });
}

export function showChunk(idx) {
  markCurrent(idx, player.playing);
  if (chunkEls[idx]) {
    chunkEls[idx].scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  progressFor(idx);
  highlightSection(idx);
}

export function showPlayState() {
  setPlayIcon(player.playing);
  markCurrent(player.idx, player.playing);
}

export function markCached(idx) {
  if (chunkEls[idx]) chunkEls[idx].classList.add('cached');
}

export function clearCachedMarks() {
  chunkEls.forEach(el => el.classList.remove('cached'));
}

function highlightSection(chunkIdx) {
  let best = null, bestCi = -1;
  $$('.section-item').forEach(item => {
    const ci = parseInt(item.dataset.chunkIdx, 10);
    item.classList.remove('active');
    if (ci <= chunkIdx && ci > bestCi) { bestCi = ci; best = item; }
  });
  if (best) best.classList.add('active');
}
