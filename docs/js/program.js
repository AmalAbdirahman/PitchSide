/* Program output feed (P13): the control room sends what is on air to docs/overlay.html over a
   BroadcastChannel, so the overlay shows exactly the state the producer sees. Same-origin tabs of
   one browser only; no network. */

import { D, S, UI, rend } from './data.js';
import { programState } from './cards.js';

export const CHANNEL = 'pitchside-program';
let channel = null;
let last = '';

function current() {
  const it = S.onAir && S.onAir.it;
  const cue = it ? rend(it.f, 'cue', UI.lang) : null;
  return programState(D.meta, D.scores, S.t, cue && { ...cue, fact_id: it.id });
}

function send(force) {
  const msg = JSON.stringify(current());
  if (!force && msg === last) return;
  last = msg;
  channel.postMessage(JSON.parse(msg));
}

/* Open the channel and answer an overlay that asks for the current state. */
export function startProgram() {
  if (!('BroadcastChannel' in window)) return;
  channel = new BroadcastChannel(CHANNEL);
  channel.onmessage = e => { if (e.data && e.data.hello) send(true); };
}

/* Called every frame; sends only when something on screen changes. */
export function publishProgram() {
  if (channel) send(false);
}
