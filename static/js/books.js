// Books mode: read the page open in Apple Books, then turn the page and keep
// going until Books stops changing (end of book) or the user stops it.
import * as api from './api.js';
import { renderDocument } from './reader.js';
import { player, state } from './state.js';
import { $, setFileName, showSectionsTab, showToast } from './ui.js';

let booksMode = false;

export function isBooksMode() {
  return booksMode;
}

// Leave Books mode without touching Books itself (never turns the page).
export function exitBooksMode() {
  booksMode = false;
  $('btn-books').classList.remove('books-active');
}

function showPage(data) {
  state.docData = data;
  setFileName(data.page_title ? `\u{1F4D6} ${data.page_title}` : 'Books — current page');
  renderDocument(data);
  player.play();
}

async function start() {
  $('btn-books').classList.add('books-active');
  showToast('Capturing Books page…');
  try {
    const data = await api.booksCapture();
    booksMode = true;
    showPage(data);
    showSectionsTab();
    showToast(`Reading ${data.total_chunks} paragraph${data.total_chunks === 1 ? '' : 's'}`, 2000);
  } catch (err) {
    exitBooksMode();
    showToast(err.message, 7000, true);
  }
}

// Called when the player reaches the end of the current page.
export async function nextPage() {
  showToast('Turning page…', 1200);
  try {
    // The server turns the page, waits for it to settle and returns it, or
    // fails with 409 if the page didn't change (so we never loop forever).
    const data = await api.booksNextPage();
    if (!booksMode) return;   // stopped while the page was turning
    showPage(data);
    showToast('Next page', 1200);
  } catch (err) {
    if (!booksMode) return;
    exitBooksMode();
    showToast(`${err.message} Books mode stopped.`, 8000, true);
  }
}

export function initBooks() {
  $('btn-books').addEventListener('click', () => {
    if (booksMode) {
      exitBooksMode();
      player.stop();
      showToast('Books mode stopped', 2000);
    } else {
      start();
    }
  });
}
