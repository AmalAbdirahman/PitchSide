/* Ask PitchSide (R5): suggested questions whose answers were built at build time and checked by
   code (app/ask.py, app/ask_checks.py). This module only reads data/ask.json: it never calls a
   model. Typed questions go to a server on the user's own machine (ask_live.js, R6).
   A question becomes available once the replay clock reaches the time it was asked at, so an
   answer never shows a later state of the match. */

import { D, S, $, clock, esc, getJson } from './data.js';

let ASK = null;          // data/ask.json, or null if it is missing
const asked = [];        // question ids clicked this session, in order
let shownKey = '';       // what renderAsk last drew, to skip redundant redraws

export async function loadAsk() {
  try {
    ASK = await getJson('data/ask.json');
  } catch (err) {
    ASK = null;  // an older bundle without ask.json: the panel says so and the rest still works
  }
}

const NUMBER = /\d+(?:\.\d+)?/g;
const minuteOf = a => a.at_minute + "'";

/* The answer text with a tick after each number the checks traced to a tool call. */
function withTicks(a) {
  const ok = a.numbers.filter(n => n.verified).map(n => n.value);
  return esc(a.text).replace(NUMBER, tok => ok.some(v => Math.abs(v - Number(tok)) < 1e-9)
    ? `<b>${tok}</b><span class="tick" aria-hidden="true">✓</span>` : tok);
}

export function answerHtml(a) {
  if (a.status === 'verified') {
    const n = a.numbers.length, ok = a.numbers.filter(x => x.verified).length;
    const chips = a.evidence_ids.map(id => `<button type="button" class="chip" data-ev="${id}" aria-label="Open event ${id} in the evidence drawer">ev ${id}</button>`).join('');
    return `<p>${withTicks(a)}</p><div class="chips">${chips}<span class="tag c-verified">${ok}/${n} numbers verified</span></div>`;
  }
  if (a.status === 'refused') {
    const near = a.closest_fact ? ` Closest verified fact: ${esc(a.closest_fact.text)}` : '';
    return `<p>${esc(a.text)}${near}</p><div class="chips"><span class="tag c-rejected">not verified</span></div>`;
  }
  return '<p class="c-muted">No answer has been built for this question yet. The Analyst runs only in a <code>--foundry</code> build.</p>';
}

function threadHtml() {
  const shown = asked.map(id => ASK.answers.find(a => a.question_id === id)).filter(a => a && a.at_t <= S.t);
  if (!shown.length) return '<p class="note">Pick a suggested question. Each one opens at the minute it was asked.</p>';
  return shown.map(a => `<div class="q">${esc(a.question)}</div>` +
    `<div class="a" data-status="${a.status}"><span class="sr-only">Answer, as of ${minuteOf(a)}: </span>${answerHtml(a)}</div>`).join('');
}

function questionsHtml() {
  return ASK.answers.map(a => {
    const open = a.at_t <= S.t;
    return `<button type="button" class="sq" data-q="${esc(a.question_id)}"${open ? '' : ' disabled'}>` +
      `${esc(a.question)}${open ? '' : ` <span class="c-muted">· opens at ${minuteOf(a)}</span>`}</button>`;
  }).join('');
}

/* Redraw when the set of open questions or the thread changes. */
export function renderAsk() {
  if (!ASK) {
    if (shownKey !== 'none') $('#ask-thread').innerHTML = '<p class="note">This bundle has no suggested answers (data/ask.json is missing).</p>';
    shownKey = 'none';
    return;
  }
  const key = ASK.answers.filter(a => a.at_t <= S.t).length + '|' + asked.join(',');
  if (key === shownKey) return;
  shownKey = key;
  $('#ask-qs').innerHTML = questionsHtml();
  $('#ask-thread').innerHTML = threadHtml();
}

/* The drawer, listing the events an answer rests on. */
function openEvents(ids, title) {
  const rows = ids.map(id => {
    const e = D.byId.get(id);
    if (!e) return `<tr><td>${id}</td><td colspan="5"><span class="tag c-rejected">not in the event log</span></td></tr>`;
    return `<tr><td class="num">${e.id}</td><td class="num">${clock(e.t)}</td><td>${esc(e.type.replace('_', ' '))}</td>` +
      `<td>${esc(e.team)}</td><td>${esc(e.player || '')}</td><td>${esc((e.outcome || '').replace('_', ' '))}</td></tr>`;
  }).join('');
  $('#dr-t').textContent = 'Evidence · ' + title;
  $('#dr-b').innerHTML = '<p class="note">Events cited by a suggested answer. The verifier checked that each came back from one of the Analyst&rsquo;s tool calls.</p>' +
    `<table><caption class="sr-only">Cited events</caption><thead><tr><th>Event</th><th>Time</th><th>Type</th><th>Team</th><th>Player</th><th>Outcome</th></tr></thead><tbody>${rows}</tbody></table>` +
    '<p class="note">All events are synthetic.</p>';
  $('#drawer').showModal();
  $('#dr-x').focus();
}

export function wireAsk() {
  $('#ask-qs').addEventListener('click', e => {
    const b = e.target.closest('button[data-q]');
    if (!b || b.disabled) return;
    const id = b.dataset.q, i = asked.indexOf(id);
    if (i >= 0) asked.splice(i, 1);
    asked.push(id);
    renderAsk();
    $('#ask-thread').lastElementChild?.scrollIntoView({ block: 'nearest' });
  });
  $('#ask-thread').addEventListener('click', onEvidenceClick);
}

/* An event-id chip in an answer opens the drawer with that answer's events, the clicked one first. */
export function onEvidenceClick(e) {
  const b = e.target.closest('button[data-ev]');
  if (!b) return;
  const id = Number(b.dataset.ev), a = b.closest('.a');
  const ids = [...a.querySelectorAll('button[data-ev]')].map(x => Number(x.dataset.ev));
  openEvents(ids.includes(id) ? [id, ...ids.filter(x => x !== id)] : [id], 'event ' + id);
}

/* Audit view: each build-time Ask run with its checks. */
export function renderAskAudit() {
  const body = $('#askt tbody');
  if (!ASK) { body.innerHTML = '<tr><td colspan="4" class="c-muted">No ask.json in this bundle.</td></tr>'; return; }
  body.innerHTML = ASK.answers.map(a => {
    const tries = a.attempts.map(t => {
      const checks = t.checks.map(c => `<span class="${c.passed ? 'c-verified' : 'c-rejected'}" title="${esc(c.detail)}">${c.passed ? '✓' : '✗'} ${esc(c.name)}</span>`).join(' ');
      return `<div>#${t.attempt} ${esc(t.outcome.replace('_', ' '))}${checks ? ': ' + checks : ''}</div>`;
    }).join('');
    const cls = { verified: 'c-verified', refused: 'c-rejected', not_built: 'c-muted' }[a.status];
    return `<tr><td class="num">${minuteOf(a)}</td><td>${esc(a.question)}</td><td class="trace">${tries}</td><td class="${cls}">${esc(a.status.replace('_', ' '))}</td></tr>`;
  }).join('');
}
