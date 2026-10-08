// State shared between modules: the one player and the open document.
import { Player } from './player.js';

export const player = new Player();

export const state = {
  docData: null,   // the open document: {doc_id, sections, chunks, total_chunks, ...}
};
