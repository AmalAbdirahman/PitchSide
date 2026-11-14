/* Live control room (P14). Follows the API's WebSocket, resolves each fact's routing state with
   the same rules as the replay (docs/js/routing.js, timed in real seconds from when the fact was
   ready), sends producer decisions to POST /decisions, and feeds the overlay
   (/docs/overlay.html) over the same BroadcastChannel as the static control room. */

import { resolve } from '/docs/js/routing.js';
import { esc } from '/docs/js/cards.js';

const $ = sel => document.querySelector(sel);
const AIRED = new Set(['auto_approved', 'approved']);
const LABEL = {
  veto_window: ['veto window', 'st-wait'], queued: ['awaiting producer', 'st-wait'], auto_approved: ['aired', 'st-aired'],
  approved: ['aired (approved)', 'st-aired'], vetoed: ['vetoed', 'st-no'], rejected: ['rejected by producer', 'st-no'],
  expired: ['expired, not aired', 'st-no'],
};
const L = { rules: null, meta: null, tick: null, facts: new Map(), decisions: {}, offset: 0, aired: new Set(), queue: [], onAir: null };
const channel = 'BroadcastChannel' in window ? new BroadcastChannel('pitchside-program') : null;

const now = () => Date.now() / 1000 + L.offset;
const cue = f => (f.renditions || []).find(r => r.kind === 'cue' && r.lang === 'en');

function log(text, cls = '') {
  const li = document.createElement('li');
  li.className = cls;
  li.textContent = `${new Date().toLocaleTimeString()} ${text}`;
  $('#log').prepend(li);
}

function stateOf(entry) {
  const f = entry.fact, d = L.decisions[f.fact_id];
  const elapsed = now() - entry.ready_at;
  return resolve(L.rules, f.risk.tier, f.approval.ttl_s || 0, f.risk.reasons || [], elapsed,
    d ? d.decision : null, d ? d.elapsed_s : null).state;
}

function decisionsFor(entry, state) {
  const rule = L.rules.tiers[entry.fact.risk.tier];
  if (state !== rule.initial.state || L.decisions[entry.fact.fact_id]) return [];
  return Object.keys(rule.decisions).filter(d => d !== 'autopilot');
}

function row(entry) {
  const f = entry.fact;
  if (entry.status !== 'ready') {
    return `<tr class="muted"><td>${esc(f.created_t ? Math.floor(f.created_t / 60) + 1 : '')}'</td><td>${esc(f.fact_id)}</td><td>–</td>` +
      `<td>${esc(f.text.canonical_en)}</td><td class="st-no">rejected by verifier</td><td></td></tr>`;
  }
  const state = stateOf(entry), [label, cls] = LABEL[state] || [state, ''];
  const buttons = decisionsFor(entry, state).map(d =>
    `<button type="button" data-id="${esc(f.fact_id)}" data-d="${esc(d)}">${esc(d[0].toUpperCase() + d.slice(1))}<span class="sr-only"> ${esc(f.fact_id)}</span></button>`).join('');
  return `<tr><td>${esc(minuteOf(f.created_t))}'</td><td>${esc(f.fact_id)}</td><td class="tier">${esc(f.risk.tier)}</td>` +
    `<td>${esc(f.text.canonical_en)}</td><td class="${cls}">${esc(label)}</td><td>${buttons}</td></tr>`;
}

function minuteOf(t) {
  if (!L.meta) return '';
  const starts = L.meta.clock.period_starts, base = [0, 45, 90, 105];
  let i = 0;
  starts.forEach((s, k) => { if (s <= t) i = k; });
  return base[i] + Math.floor((t - starts[i]) / 60) + 1;
}

function render() {
  if (L.tick && L.meta) {
    $('#home').textContent = L.meta.home;
    $('#away').textContent = L.meta.away;
    $('#score').textContent = `${L.tick.score[L.meta.home]}–${L.tick.score[L.meta.away]}`;
    $('#minute').textContent = `${L.tick.minute}'`;
  }
  const entries = [...L.facts.values()].reverse();
  if (entries.length) $('#rows').innerHTML = entries.map(row).join('');
}

/* Air every fact whose state has become aired; one graphic at a time, newest waiting wins. */
function airTick() {
  if (!L.rules) return;
  for (const entry of L.facts.values()) {
    const id = entry.fact.fact_id;
    if (entry.status === 'ready' && !L.aired.has(id) && AIRED.has(stateOf(entry))) {
      L.aired.add(id);
      L.queue = [entry];
      log(`Aired ${id}`, 'st-aired');
    }
  }
  if (L.onAir && Date.now() / 1000 > L.onAir.until) L.onAir = null;
  if (!L.onAir && L.queue.length) {
    const entry = L.queue.shift(), c = cue(entry.fact);
    L.onAir = { entry, until: Date.now() / 1000 + (c ? c.duration_s : 6) };
  }
  const c = L.onAir && cue(L.onAir.entry.fact);
  $('#onair').innerHTML = c ? `<div class="card"><b>${esc(c.layout.replace('_', ' '))}</b><p>${esc(c.text)}</p></div>` : '<p class="muted">Nothing on air.</p>';
  publish(c ? { ...c, fact_id: L.onAir.entry.fact.fact_id } : null);
}

let lastSent = '';
function publish(c) {
  if (!channel || !L.meta || !L.tick) return;
  const team = code => ({ code, name: L.meta.teams[code].name, score: L.tick.score[code] });
  const msg = {
    v: 1, minute: L.tick.minute, home: team(L.meta.home), away: team(L.meta.away),
    cue: c ? { fact_id: c.fact_id, lang: c.lang, layout: c.layout, card: c.card } : null,
  };
  const text = JSON.stringify(msg);
  if (text !== lastSent) { lastSent = text; channel.postMessage(msg); }
}
if (channel) channel.onmessage = e => { if (e.data && e.data.hello) { lastSent = ''; airTick(); } };

async function decide(id, decision) {
  const res = await fetch('/decisions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fact_id: id, decision }) });
  const body = await res.json();
  if (!res.ok) log(`${id} ${decision}: ${body.detail}`, 'st-no');
}

function onMessage(m) {
  if (m.type === 'snapshot') {
    Object.assign(L, { rules: m.routing, meta: m.meta, tick: m.tick, decisions: m.decisions, offset: m.server_now - Date.now() / 1000 });
    L.facts = new Map(m.facts.map(e => [e.fact.fact_id, e]));
    for (const e of L.facts.values()) if (e.status === 'ready' && AIRED.has(stateOf(e))) L.aired.add(e.fact.fact_id);
    log(`Connected: ${m.facts.length} facts so far${m.ended ? ', match over' : ''}`);
  } else if (m.type === 'start') {
    L.meta = m.meta;
    log(`Match ${m.match_id} started`);
  } else if (m.type === 'tick') {
    L.tick = m;
  } else if (m.type === 'fact') {
    L.facts.set(m.fact.fact_id, m);
    log(`${m.fact.fact_id} ${m.status === 'ready' ? 'routed ' + m.fact.risk.tier : 'rejected by verifier'}`, m.status === 'ready' ? '' : 'st-no');
  } else if (m.type === 'decision') {
    L.decisions[m.fact_id] = m;
    log(`Producer ${m.decision} on ${m.fact_id}: ${m.state}`, m.state === 'approved' ? 'st-aired' : 'st-no');
  } else if (m.type === 'end') {
    log('Full time');
  }
}

function connect(delay = 500) {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onopen = () => { $('#conn').textContent = 'connected'; $('#conn').className = 'on'; delay = 500; };
  ws.onmessage = e => onMessage(JSON.parse(e.data));
  ws.onclose = () => {
    $('#conn').textContent = 'disconnected, retrying…';
    $('#conn').className = '';
    setTimeout(() => connect(Math.min(delay * 2, 8000)), delay);
  };
}

$('#rows').addEventListener('click', e => {
  const b = e.target.closest('button[data-d]');
  if (b) decide(b.dataset.id, b.dataset.d);
});
setInterval(() => { airTick(); render(); }, 250);
connect();
