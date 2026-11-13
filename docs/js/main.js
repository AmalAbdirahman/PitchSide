/* Pitchside control room entry point: loads the bundle, runs the frame loop and wires the controls. */

import { D, S, UI, $, $$, esc, idxAt, loadBundle, setState } from './data.js';
import { advance, decide, freshState, oldestFor, setAutopilot, tickOnAir, tickTimers } from './state.js';
import { drawPitch, drawRibbon, flash, popScore, renderClock, renderFeed, renderPitch, renderZones } from './pitch.js';
import { openEvidence, renderOnAir, renderQueue, renderTimers } from './queue.js';
import { renderAudit, renderLog } from './audit.js';
import { maybeRenderAnalysis } from './analysis.js';
import { publishProgram, startProgram } from './program.js';

async function load() {
  try {
    await loadBundle();
  } catch (err) {
    const box = $('#load-error');
    box.hidden = false;
    box.innerHTML = '<b>Could not load the replay bundle.</b> ' + esc(err.message) +
      '<br>Serve the folder over HTTP, for example <code>python -m http.server 8000 --directory docs</code>, then open <code>localhost:8000</code> in a browser.';
    return;
  }
  setState(freshState());
  startProgram();
  setupStatic();
  wire();
  renderAll();
  requestAnimationFrame(frame);
}

/* ---------- static parts ---------- */
function setupStatic() {
  const { home, away, teams } = D.meta;
  $('#home-name').textContent = teams[home].name.toUpperCase();
  $('#away-name').textContent = teams[away].name.toUpperCase();
  $('#home-ab').textContent = $('#bug-h').textContent = home;
  $('#away-ab').textContent = $('#bug-a').textContent = away;
  $('#wp-name').textContent = home;
  drawPitch();
  drawRibbon();
  fillFollow();
  fillRules();
  setupSpeech();
}

function fillFollow() {
  const sel = $('#follow');
  for (const team of [D.meta.home, D.meta.away]) {
    const grp = document.createElement('optgroup');
    grp.label = D.meta.teams[team].name;
    for (const [id, name] of Object.entries(D.meta.teams[team].players)) {
      const n = D.items.filter(it => it.players.includes(id)).length;
      grp.appendChild(new Option(`${name} (${n} fact${n === 1 ? '' : 's'})`, id));
    }
    sel.appendChild(grp);
  }
}

/* Rule text from the routing table in the bundle, so the page states the timeouts it enforces. */
function fillRules() {
  const { T1, T2 } = D.meta.routing.tiers;
  $('#rule-t1').textContent = `Airs after a ${T1.ttl_s} s veto window`;
  $('#rule-t2').textContent = `Producer approves within ${T2.ttl_s} s or it is dropped`;
}

function localVoice(lang) {
  return speechSynthesis.getVoices().find(v => v.localService && v.lang.toLowerCase().startsWith(lang));
}

function setupSpeech() {
  if (!('speechSynthesis' in window)) return;
  const update = () => { $('#speak').hidden = !localVoice('en'); };
  update();
  speechSynthesis.addEventListener('voiceschanged', update);
}

function speak() {
  const text = $('#aud-t').textContent, voice = localVoice('en');
  if (!voice || !text) return;
  const u = new SpeechSynthesisUtterance(text);
  u.voice = voice;
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
}

/* ---------- loop ---------- */
const EFFECTS = { flash, pop: popScore, ended: setPlayButton };

let lastNow = performance.now();
function frame(now) {
  const dt = Math.min(0.25, Math.max(0, (now - lastNow) / 1000));  // rAF stamps can precede lastNow
  lastNow = now;
  if (S.playing) advance(dt);
  if (S.playing || S.t >= D.end) {
    tickTimers(dt);
    tickOnAir(dt);
  }
  for (const fx of S.fx.splice(0)) EFFECTS[fx]();
  if (S.dirty) renderAll();
  if (S.feedDirty) renderFeed();
  if (idxAt(S.t) !== S.zoneIdx) {
    S.zoneIdx = idxAt(S.t);
    renderZones();
  }
  if (UI.view === 'an') maybeRenderAnalysis(now);
  renderClock();
  renderTimers();
  publishProgram();
  requestAnimationFrame(frame);
}

function renderAll() {
  S.dirty = false;
  renderQueue();
  renderOnAir();
  renderLog();
  renderAudit();
  renderPitch();
  renderZones();
  S.anKey = null;
}

/* ---------- controls ---------- */
function setPlayButton() {
  const b = $('#play');
  b.textContent = S.playing ? '❚❚' : '▶';
  b.setAttribute('aria-label', S.playing ? 'Pause' : 'Play');
}

function restart() {
  setState(freshState());
  $('#lt').classList.remove('show');
  renderFeed();
  renderAll();
}

function togglePlay() {
  if (S.t >= D.end) restart();
  S.playing = !S.playing;
  setPlayButton();
}

function setView(v) {
  UI.view = v;
  $$('nav.views button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.view === v)));
  for (const k of ['bc', 'an', 'au']) $('#view-' + k).hidden = k !== v;
  S.anKey = null;
}

function act(id, decision) {
  decide(id, decision);
  renderAll();
}

const KEY_DECISION = { a: 'approve', r: 'reject', v: 'veto' };

function onKey(e) {
  if (e.ctrlKey || e.metaKey || e.altKey || /SELECT|INPUT|TEXTAREA/.test(e.target.tagName)) return;
  if ($('#drawer').open || $('#aboutdlg').open) return;
  const k = e.key.toLowerCase();
  if (e.code === 'Space' && e.target.tagName !== 'BUTTON' && e.target.tagName !== 'A') { e.preventDefault(); togglePlay(); }
  else if (KEY_DECISION[k]) { const it = oldestFor(KEY_DECISION[k]); if (it) act(it.id, KEY_DECISION[k]); }
  else if (k === '1') setView('bc');
  else if (k === '2') setView('an');
  else if (k === '3') setView('au');
}

function press(sel, fn) {
  $$(sel).forEach(b => b.addEventListener('click', () => {
    $$(sel).forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    fn(b);
    S.dirty = true;
  }));
}

function wire() {
  $('#play').addEventListener('click', togglePlay);
  $('#restart').addEventListener('click', () => { restart(); S.playing = true; setPlayButton(); });
  press('[data-speed]', b => { UI.speed = Number(b.dataset.speed); });
  press('[data-depth]', b => { UI.depth = b.dataset.depth; document.body.dataset.mode = UI.depth; });
  press('[data-lang]', b => { UI.lang = b.dataset.lang; });
  $$('nav.views button').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));
  $('#follow').addEventListener('change', e => { UI.follow = e.target.value; S.dirty = true; });
  $('#auto').addEventListener('change', e => setAutopilot(e.target.checked));
  $('#rows').addEventListener('click', e => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    if (b.dataset.act === 'ev') openEvidence(b.dataset.id);
    else act(b.dataset.id, b.dataset.act);
  });
  $('#lt-why').addEventListener('click', () => { if (S.lastAired) openEvidence(S.lastAired.id); });
  $('#dr-x').addEventListener('click', () => $('#drawer').close());
  $('#about').addEventListener('click', () => { $('#aboutdlg').showModal(); $('#ab-x').focus(); });
  $('#ab-x').addEventListener('click', () => $('#aboutdlg').close());
  $('#speak').addEventListener('click', speak);
  document.addEventListener('keydown', onKey);
}

load();
