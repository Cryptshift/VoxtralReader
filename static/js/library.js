// The History tab: documents opened before, persisted by the server.
import * as api from './api.js';
import { clearDocument, renderDocument } from './reader.js';
import { state } from './state.js';
import { $, fmtTime, setFileName, showSectionsTab, showToast } from './ui.js';

let docHistory = [];   // [{doc_id, filename, timestamp: Date, total_chunks, data|null}]

export async function loadHistory() {
  try {
    const entries = await api.getHistory();
    const byId = new Map();   // one entry per doc_id, latest wins
    entries.forEach(e => { byId.delete(e.doc_id); byId.set(e.doc_id, e); });
    docHistory = [...byId.values()].map(e => ({
      ...e,
      timestamp: new Date(e.timestamp),
      data: null,   // loaded lazily on first switch
    }));
    renderHistory();
  } catch {}
}

// Record a freshly uploaded document and persist it (fire and forget).
export function addToHistory(entry) {
  docHistory = docHistory.filter(h => h.doc_id !== entry.doc_id);
  docHistory.push(entry);
  api.saveHistory(entry).catch(() => {});
  renderHistory();
}

export function renderHistory() {
  const empty = $('history-empty');
  const list  = $('history-list');

  // Remove all existing items (keep the empty notice)
  list.querySelectorAll('.hist-item').forEach(el => el.remove());

  if (docHistory.length === 0) {
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';

  // Newest first
  [...docHistory].reverse().forEach(entry => {
    const item = document.createElement('div');
    item.className = 'hist-item' +
      (state.docData && state.docData.doc_id === entry.doc_id ? ' active-doc' : '');
    item.dataset.docId = entry.doc_id;
    // Built with textContent (never innerHTML): file names are user-controlled.
    const body = document.createElement('div');
    body.className = 'hist-item-body';
    const name = document.createElement('div');
    name.className   = 'hist-name';
    name.title       = entry.filename;
    name.textContent = entry.filename;
    const meta = document.createElement('div');
    meta.className   = 'hist-meta';
    meta.textContent = `${entry.total_chunks} paragraphs • ${fmtTime(entry.timestamp)}`;
    body.append(name, meta);
    const del = document.createElement('button');
    del.className   = 'hist-del';
    del.title       = 'Remove';
    del.textContent = '✕';
    item.append(body, del);

    body.addEventListener('click', () => switchDocument(entry));

    del.addEventListener('click', async e => {
      e.stopPropagation();
      await api.deleteHistory(entry.doc_id);
      docHistory = docHistory.filter(h => h.doc_id !== entry.doc_id);
      if (state.docData && state.docData.doc_id === entry.doc_id) clearDocument();
      renderHistory();
    });

    list.appendChild(item);
  });
}

async function switchDocument(entry) {
  // Lazy-load full doc data if we only have metadata (loaded from history on startup)
  if (!entry.data) {
    showToast('Loading document…');
    try {
      entry.data = await api.getHistoryDocument(entry.doc_id);
    } catch {
      showToast('Could not load document data', 3000, true);
      return;
    }
  }
  await api.restoreDocument(entry.data);
  state.docData = entry.data;
  setFileName(entry.filename);
  renderDocument(entry.data);
  showSectionsTab();
  renderHistory();
}
