/* Ask PitchSide live mode (R6). The one network address in docs/: the local server that
   scripts/ask_server.py starts on this machine. Nothing here makes a request until the
   "Live (local)" tab is clicked; the page never holds a key. The server runs the same Analyst
   and the same code checks as the build-time answers, at the current replay time, and returns
   the same JSON shape, so answers are drawn with ask.js's answerHtml. */

import { S, $, esc } from './data.js';
import { answerHtml, onEvidenceClick } from './ask.js';

export const LIVE_URL = 'http://127.0.0.1:8765';  // = config.yaml ask.live.port (tests/test_site.py)
const START = 'python scripts/ask_server.py';
const HEALTH_MS = 3000;

/* Pure: what the panel shows in each state. */
export function liveView(state, info = {}) {
  switch (state) {
    case 'checking':
      return { input: false, status: '', hint: 'Looking for the local server…' };
    case 'up':
      return { input: true, status: 'Live · running on this machine',
        hint: `Up to ${info.max_chars} characters. Answers are checked by the verifier before they show.` };
    case 'asking':
      return { input: false, status: 'Live · running on this machine', hint: 'Answering and checking…' };
    case 'down':
      return { input: false, status: '',
        hint: `No local server answered. Start it with ${START} (it needs your .env), serve docs/ on port 8000, then click Live (local) again.` };
    default:  // 'built'
      return { input: false, status: '', hint: 'Live questions run locally. Click Live (local) to connect to a server on this machine.' };
  }
}

/* Pure: the trimmed question, or null if it is empty or too long. */
export function cleanQuestion(text, maxChars) {
  const q = String(text).trim();
  return q && q.length <= maxChars ? q : null;
}

let mode = 'built';
let connected = false;
let info = { max_chars: 200 };
const live = [];  // {question, answer|null, error|null} in the order asked

function show(state) {
  const v = liveView(state, info);
  $('#ask-text').disabled = !v.input;
  $('#ask-send').disabled = !v.input;
  $('#ask-hint').textContent = v.hint;
  $('#ask-status').textContent = v.status;
  $('#ask-status').hidden = !v.status;
}

function setMode(next) {
  mode = next;
  $('#ask-built').setAttribute('aria-pressed', String(next === 'built'));
  $('#ask-live').setAttribute('aria-pressed', String(next === 'live'));
  for (const id of ['#ask-built-part', '#ask-thread', '#ask-qs-part']) $(id).hidden = next !== 'built';
  $('#ask-live-thread').hidden = next !== 'live';
}

function renderLive() {
  $('#ask-live-thread').innerHTML = live.length ? live.map(x => `<div class="q">${esc(x.question)}</div>` + (
    x.answer ? `<div class="a" data-status="${x.answer.status}"><span class="sr-only">Live answer, as of ${x.answer.at_minute}': </span>${answerHtml(x.answer)}</div>`
      : x.error ? `<div class="a" data-status="error"><p>${esc(x.error)}</p></div>`
        : '<div class="a"><p class="c-muted">Answering and checking…</p></div>')).join('')
    : '<p class="note">Ask about the match up to the current replay minute.</p>';
}

/* The only requests to the local server. Called from the click and submit handlers below. */
async function callLocal(path, init, ms) {
  const res = await fetch(LIVE_URL + path, { ...init, signal: AbortSignal.timeout(ms) });
  const doc = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(doc.error || `The local server answered ${res.status}.`);
  return doc;
}

async function connect() {
  setMode('live');
  renderLive();
  show('checking');
  try {
    info = await callLocal('/health', {}, HEALTH_MS);
    $('#ask-text').maxLength = info.max_chars;
    connected = true;
    show('up');
    $('#ask-text').focus();
  } catch (err) {
    connected = false;
    show('down');  // not running, blocked by the browser, or not this page's origin
  }
}

async function ask(e) {
  e.preventDefault();
  const q = cleanQuestion($('#ask-text').value, info.max_chars);
  if (mode !== 'live' || !q) return;
  const item = { question: q, answer: null, error: null };
  live.push(item);
  renderLive();
  show('asking');
  try {
    const body = JSON.stringify({ question: q, at_t: S.t });
    item.answer = await callLocal('/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body }, (info.timeout_s + 5) * 1000);
    $('#ask-text').value = '';
  } catch (err) {
    item.error = err.name === 'TimeoutError' ? 'No answer in time.' : err.message;
  }
  renderLive();
  show('up');
  $('#ask-live-thread').lastElementChild?.scrollIntoView({ block: 'nearest' });
  $('#ask-text').focus();
}

export function wireLive() {
  $('#ask-live').addEventListener('click', () => { if (mode !== 'live' || !connected) connect(); });
  $('#ask-built').addEventListener('click', () => { setMode('built'); connected = false; show('built'); });
  $('#ask-form').addEventListener('submit', ask);
  $('#ask-live-thread').addEventListener('click', onEvidenceClick);
  show('built');
}
