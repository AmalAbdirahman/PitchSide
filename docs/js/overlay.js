/* Program output (P13): draws the score bug and the on-air card, nothing else.
   Live: listens to the control room on a BroadcastChannel (docs/js/program.js).
   ?demo=<layout>[&lang=es]: shows the first cue of that layout from the bundle, to place the
   source in OBS or take a screenshot. ?bg=1 adds a backdrop; ?clean=1 hides the waiting hint. */

import { bugHtml, cardHtml, programState, sideOf } from './cards.js';

const CHANNEL = 'pitchside-program';
const params = new URLSearchParams(location.search);
const $ = sel => document.querySelector(sel);
let meta = null;  // team codes for colours; from the first message or the bundle
let shownKey = '';

function draw(st) {
  $('#hint').hidden = true;
  $('#bug').innerHTML = bugHtml(st);
  $('#bug').hidden = false;
  const key = st.cue ? `${st.cue.fact_id}:${st.cue.lang}` : '';
  if (key === shownKey) return;
  shownKey = key;
  const side = st.cue && meta ? sideOf(st.cue.card, meta) : '';
  $('#graphic').innerHTML = st.cue ? cardHtml(st.cue.card, side) : '';
  if (st.cue) $('#graphic').lang = st.cue.lang;
}

function fit() {
  const k = Math.min(innerWidth / 1920, innerHeight / 1080);
  document.documentElement.style.setProperty('--k', String(k));
}

function live() {
  if (!('BroadcastChannel' in window)) {
    $('#hint').textContent = 'This browser has no BroadcastChannel, so the overlay cannot follow the control room.';
    return;
  }
  const ch = new BroadcastChannel(CHANNEL);
  ch.onmessage = e => {
    const st = e.data;
    if (!st || st.v !== 1) return;
    meta = { home: st.home.code, away: st.away.code };
    draw(st);
  };
  ch.postMessage({ hello: true });
}

async function demo(layout) {
  const lang = params.get('lang') === 'es' ? 'es' : 'en';
  const hero = await (await fetch('data/hero.json')).json();
  const f = hero.facts.find(x => x.renditions.some(r => r.kind === 'cue' && r.lang === lang && r.layout === layout));
  if (!f) { $('#hint').textContent = `No cue with layout ${layout} in this bundle.`; return; }
  const cue = { ...f.renditions.find(r => r.kind === 'cue' && r.lang === lang), fact_id: f.fact_id };
  meta = hero.meta;
  draw(programState(hero.meta, hero.score_timeline, f.approval.valid_from_t, cue));
}

fit();
addEventListener('resize', fit);
if (params.get('bg') === '1') document.body.classList.add('backdrop');
if (params.get('clean') === '1') $('#hint').hidden = true;
if (params.get('demo')) demo(params.get('demo'));
else live();
