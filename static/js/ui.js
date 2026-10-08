// Small DOM helpers and page-wide widgets: toast, header status, sidebar
// tabs, progress bar, formatting and the desktop-window controls.

export const $  = id => document.getElementById(id);
export const $$ = sel => document.querySelectorAll(sel);

// ── Toast ────────────────────────────────────────────────────────────────────
let toastTimer = null;
export function showToast(msg, dur = 3000, isErr = false) {
  const t = $('toast');
  t.textContent = msg;
  t.className   = 'show' + (isErr ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = ''; }, dur);
}

// ── Formatting ───────────────────────────────────────────────────────────────
export function fmtTime(date) {
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return sameDay
    ? date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' +
      date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function fmtBytes(n) {
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  if (n < 1024 ** 3)   return `${(n / 1024 ** 2).toFixed(n < 10 * 1024 ** 2 ? 1 : 0)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

// ── Sidebar tabs ─────────────────────────────────────────────────────────────
export function initTabs() {
  $$('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      $$('.tab-btn').forEach(b => b.classList.remove('active'));
      $$('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      $(`${btn.dataset.tab}-list`).classList.add('active');
    });
  });
}

export function showSectionsTab() {
  $$('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === 'sections'));
  $$('.tab-panel').forEach(p => p.classList.toggle('active', p.id === 'section-list'));
}

// ── Header ───────────────────────────────────────────────────────────────────
export function setFileName(text) {
  $('file-name').textContent = text;
}

export function setServerStatus(health) {
  const dot = $('status-dot');
  const lbl = $('status-label');
  if (!health) {
    dot.className   = 'bad';
    lbl.textContent = 'server error';
    return;
  }
  dot.className   = health.tts ? 'ok' : 'bad';
  lbl.textContent = health.tts ? 'TTS ready' : 'TTS offline';
}

// ── Player bar ───────────────────────────────────────────────────────────────
export function setTransportEnabled(enabled) {
  for (const id of ['btn-play-pause', 'btn-stop', 'btn-prev', 'btn-next']) {
    $(id).disabled = !enabled;
  }
}

export function setPlayIcon(playing) {
  $('icon-play').style.display  = playing ? 'none' : '';
  $('icon-pause').style.display = playing ? '' : 'none';
}

export function setLoading(flag) {
  $('spinner').classList.toggle('visible', flag);
}

export function updateProgress(idx, total, section) {
  const pct = total > 1 ? (idx / (total - 1)) * 100 : 0;
  $('progress-fill').style.width = pct + '%';
  $('progress-label').textContent = total ? `${idx + 1} / ${total}` : '—';
  if (section !== undefined) $('section-label').textContent = section || '';
}

export function setChunkProgress(cur, dur) {
  if (!dur) return;
  $('chunk-progress').style.width = (cur / dur) * 100 + '%';
}

export function setRealTimeFactor(rtf, genMs) {
  const el = $('rtf-indicator');
  el.textContent = rtf.toFixed(1) + '× RT';
  el.title = `Real-time factor: ${rtf.toFixed(2)}× (generated in ${(genMs / 1000).toFixed(1)}s)`;
  el.className = 'rtf-indicator ' + (rtf >= 2 ? 'fast' : rtf >= 1 ? 'ok' : 'slow');
}

// ── Desktop window controls (pywebview, run.py) ──────────────────────────────
function pywebviewApi() {
  return (typeof window !== 'undefined' && window.pywebview?.api) || null;
}

export function initWindowControls() {
  // Close/minimize/pin only work in the desktop window; CSS hides them in a
  // normal browser tab unless <body> has the in-webview class.
  const markWebview = () => {
    if (window.pywebview) document.body.classList.add('in-webview');
  };
  window.addEventListener('pywebviewready', markWebview);
  markWebview();

  $('btn-wc-close').addEventListener('click', () => pywebviewApi()?.close());
  $('btn-wc-minimize').addEventListener('click', () => pywebviewApi()?.minimize());

  let pinned = true;
  $('btn-pin').addEventListener('click', () => {
    pinned = !pinned;
    $('btn-pin').classList.toggle('pinned', pinned);
    $('btn-pin').title = pinned ? 'Unpin (disable always on top)' : 'Pin (always on top)';
    pywebviewApi()?.set_on_top(pinned);
  });
}
