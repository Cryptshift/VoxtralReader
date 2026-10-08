// Thin client for the Voxtral Reader server API. Functions throw an Error
// whose message is the server's `detail` when a request fails.

async function detail(r, fallback) {
  const err = await r.json().catch(() => ({}));
  return typeof err.detail === 'string' ? err.detail : fallback;
}

async function json(r, fallback) {
  if (!r.ok) throw new Error(await detail(r, fallback));
  return r.json();
}

const postJson = (path, body) => fetch(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

// ── Documents and audio ──────────────────────────────────────────────────────

export function audioUrl(docId, idx, voice) {
  return `/audio/${encodeURIComponent(docId)}/${idx}?voice=${encodeURIComponent(voice)}`;
}

export async function audioErrorMessage(r) {
  return (await detail(r, null)) || `HTTP ${r.status}`;
}

export async function uploadPdf(file, includeRefs) {
  const form = new FormData();
  form.append('file', file);
  const r = await fetch(`/upload?include_refs=${includeRefs}`, { method: 'POST', body: form });
  return json(r, 'Upload failed');
}

// Re-register a document with the server (e.g. after it restarted).
export async function restoreDocument(data) {
  const r = await postJson('/restore', data);
  return r.ok;
}

// ── History ──────────────────────────────────────────────────────────────────

export async function getHistory() {
  return json(await fetch('/history'), 'Could not load history');
}

export async function getHistoryDocument(docId) {
  return json(await fetch(`/history/${docId}`), 'Could not load document data');
}

export function saveHistory(entry) {
  return postJson('/history', {
    doc_id:       entry.doc_id,
    filename:     entry.filename,
    timestamp:    entry.timestamp.toISOString(),
    total_chunks: entry.total_chunks,
    data:         entry.data,
  });
}

export function deleteHistory(docId) {
  return fetch(`/history/${docId}`, { method: 'DELETE' });
}

// ── Server info ──────────────────────────────────────────────────────────────

export async function getHealth() {
  return (await fetch('/health')).json();
}

export async function getVoices() {
  return (await fetch('/voices')).json();
}

export async function getCacheStats() {
  return (await fetch('/cache')).json();
}

export function clearCache() {
  return fetch('/cache', { method: 'DELETE' });
}

// ── Books mode ───────────────────────────────────────────────────────────────

export async function booksCapture() {
  return json(await fetch('/books/capture', { method: 'POST' }), 'Could not capture Books page');
}

// Turns the page, waits for it to settle and returns it; fails with 409 if
// the page didn't change.
export async function booksNextPage() {
  return json(await fetch('/books/next_page', { method: 'POST' }), 'Could not load the next page');
}
