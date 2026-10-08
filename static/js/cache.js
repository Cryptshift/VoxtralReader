// "Audio cache: <size> of <cap>" readout and Clear button in the sidebar.
import * as api from './api.js';
import { $, fmtBytes, showToast } from './ui.js';

let cacheBytes = 0;
let refreshTimer = null;

export async function refreshCacheStats() {
  try {
    const j = await api.getCacheStats();
    cacheBytes = j.bytes;
    $('cache-size').textContent = j.max_bytes > 0
      ? `Audio cache: ${fmtBytes(j.bytes)} of ${fmtBytes(j.max_bytes)}`
      : 'Audio cache: off';
    $('btn-clear-cache').disabled = j.files === 0;
  } catch {}
}

// Coalesce refreshes while several paragraphs finish generating.
export function scheduleCacheRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refreshCacheStats, 1500);
}

export function initCacheControls() {
  $('btn-clear-cache').addEventListener('click', async () => {
    if (!confirm(`Delete ${fmtBytes(cacheBytes)} of cached audio? Paragraphs will be generated again when you replay them.`)) return;
    try {
      await api.clearCache();
      showToast('Audio cache cleared', 2000);
    } catch {
      showToast('Could not clear the audio cache', 4000, true);
    }
    refreshCacheStats();
  });
}
