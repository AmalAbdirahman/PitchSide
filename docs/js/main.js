/* Pitchside control room entry point: loads the bundle, runs the frame loop and wires the controls. */

import { D, S, UI, $, $$, esc, idxAt, loadBundle, readColours, setState } from './data.js';
import { advance, decide, freshState, oldestFor, setAutopilot, tickOnAir, tickTimers } from './state.js';
import { drawPitch, drawRibbon, flash, popScore, renderClock, renderFeed, renderPitch, renderZones } from './pitch.js';
import { openEvidence, renderOnAir, renderQueue, renderTimers } from './queue.js';
import { renderAudit, renderLog } from './audit.js';
import { maybeRenderAnalysis } from './analysis.js';
import { publishProgram, startProgram } from './program.js';
import { renderKpis, setupKpis } from './kpi.js';
import { renderStats } from './matchstats.js';
import { loadAsk, renderAsk, renderAskAudit, wireAsk } from './ask.js';

async function load() {
  try {
    await loadBundle();
    await loadAsk();
  } catch (err) {
    const box = $('#load-error');
    box.hidden = false;
    box.innerHTML = '<b>Could not load the replay bundle.</b> ' + esc(err.message) +
      '<br>Serve the folder over HTTP, for example <code>python -m http.server 8000 --directory docs</code>, then open <code>localhost:8000</code> in a browser.';
    return;
  }
  setState(freshState());
  readColours();
  startProgram();
  setupStatic();
  wire();
  renderAll();
  requestAnimationFrame(frame);
}

/* ---------- static parts ---------- */
function setupStatic() {
  const { home, away, teams } = D.meta;
  $('#home-name').textContent = teams[home].name;
  $('#away-name').textContent = teams[away].name;
  $('#home-ab').textContent = $('#bug-h').textContent = home;
  $('#away-ab').textContent = $('#bug-a').textContent = away;
  $('#wp-name').textContent = home;
  setupKpis();
  drawPitch();
  drawRibbon();
  fillFollow();
  fillRules();
  setupSpeech();
  renderAskAudit();
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
    renderKpis();
    renderStats();
  }
  if (UI.view === 'an') maybeRenderAnalysis(now);
  renderAsk();
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
  renderKpis();
  renderStats();
  renderAsk();
  S.anKey = null;
}

/* ---------- controls ---------- */
function setPlayButton() {
  const b = $('#play');
  b.textContent = S.playing ? '❚❚' : '▶';
  b.setAttribute('aria-label', S.playing ? 'Pause' : 'Play');
  document.body.classList.toggle('playing', S.playing);
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
  if ($$('dialog').some(d => d.open)) return;
  const k = e.key.toLowerCase();
  if (e.code === 'Space' && e.target.tagName !== 'BUTTON' && e.target.tagName !== 'A') { e.preventDefault(); togglePlay(); }
  else if (KEY_DECISION[k]) { const it = oldestFor(KEY_DECISION[k]); if (it) act(it.id, KEY_DECISION[k]); }
  else if (k === '1') setView('bc');
  else if (k === '2') setView('an');
  else if (k === '3') setView('au');
}

/* A header pill that steps through values: shows the current one and names the next. */
function cycle(sel, values, label, apply) {
  const b = $(sel);
  let k = 0;
  const show = () => {
    const [value, text] = values[k], next = values[(k + 1) % values.length][1];
    b.querySelector('b').textContent = text;
    b.setAttribute('aria-label', `${label}: ${text}. Switch to ${next}.`);
    apply(value);
  };
  b.addEventListener('click', () => { k = (k + 1) % values.length; show(); S.dirty = true; });
  show();
}

function showLatestEvidence() {
  const it = S.lastAired || [...D.items].reverse().find(x => S.rt[x.id]);
  if (it) return openEvidence(it.id);
  $('#dr-t').textContent = 'Evidence';
  $('#dr-b').innerHTML = '<p class="note">Nothing drafted yet. Press play, then open the evidence for a fact.</p>';
  $('#drawer').showModal();
  $('#dr-x').focus();
}

function openDialog(id, close) {
  $(id).showModal();
  $(close).focus();
}

function wire() {
  $('#play').addEventListener('click', togglePlay);
  $('#restart').addEventListener('click', () => { restart(); S.playing = true; setPlayButton(); });
  cycle('#speed', [[1, '1×'], [2, '2×'], [4, '4×']], 'Replay speed', v => { UI.speed = v; $('#speed').dataset.speed = v; });
  cycle('#depth', [['casual', 'Casual'], ['analyst', 'Analyst']], 'Viewer depth', v => { UI.depth = v; document.body.dataset.mode = v; });
  cycle('#lang', [['en', 'EN'], ['es', 'ES']], 'Language', v => { UI.lang = v; });
  $$('nav.views button').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));
  $('#follow').addEventListener('change', e => { UI.follow = e.target.value; S.dirty = true; });
  $('#auto').addEventListener('click', () => {
    const on = !UI.auto;
    $('#auto').setAttribute('aria-pressed', String(on));
    $('#auto b').textContent = on ? 'On' : 'Off';
    setAutopilot(on);
  });
  $('#go-cues').addEventListener('click', () => {
    setView('bc');
    $('#cues').scrollIntoView({ block: 'center' });
    $('#cues').focus({ preventScroll: true });
  });
  $('#go-ask').addEventListener('click', () => {
    $('#ask').scrollIntoView({ block: 'nearest' });
    $('#ask').focus({ preventScroll: true });
  });
  wireAsk();
  $('#go-ev').addEventListener('click', showLatestEvidence);
  $('#go-set').addEventListener('click', () => openDialog('#setdlg', '#set-x'));
  $('#set-x').addEventListener('click', () => $('#setdlg').close());
  $('#rows').addEventListener('click', e => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    if (b.dataset.act === 'ev') openEvidence(b.dataset.id);
    else act(b.dataset.id, b.dataset.act);
  });
  $('#lt-why').addEventListener('click', () => { if (S.lastAired) openEvidence(S.lastAired.id); });
  $('#dr-x').addEventListener('click', () => $('#drawer').close());
  $('#about').addEventListener('click', () => openDialog('#aboutdlg', '#ab-x'));
  $('#ab-x').addEventListener('click', () => $('#aboutdlg').close());
  $('#speak').addEventListener('click', speak);
  document.addEventListener('keydown', onKey);
  document.addEventListener('themechange', redrawForTheme);
}

/* SVG drawings take their colours from the CSS tokens when drawn, so a theme change redraws them. */
function redrawForTheme() {
  readColours();
  drawPitch();
  drawRibbon();
  renderFeed();
  renderAll();
  renderClock();
}

load();
