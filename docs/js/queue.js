/* Producer queue, lower third, outputs panel and evidence drawer. Buttons come from the decisions
   each tier allows in meta.routing; nothing here decides state (see state.js). */

import { D, S, UI, CUES, $, esc, clock, minute, rend } from './data.js';
import { PHASE, actionable, producerDecisions, tierRule, timeLeft } from './state.js';

const TIER_CLS = { T0: 'c-green', T1: 'c-cyan', T2: 'c-amber' };

function ltLabel(it) {
  const es = UI.lang === 'es';
  if (it.f.kind === 'score_event') return es ? 'Gol' : 'Goal';
  if (it.tier === 'T0') return es ? 'Evento' : 'Match event';
  return es ? 'Dato verificado' : 'Verified';
}

function checksPassed(it) {
  const ch = it.f.verification.checks;
  return `${ch.filter(c => c.passed).length}/${ch.length} checks`;
}

export function renderOnAir() {
  const lt = $('#lt'), cur = S.onAir && S.onAir.it;
  if (cur) {
    const k = $('#lt-k');
    k.textContent = ltLabel(cur);
    k.className = 'k' + (cur.f.kind === 'score_event' ? ' goal' : '');
    $('#lt-t').textContent = rend(cur.f, 'cue', UI.lang).text;
    $('#lt-t').lang = UI.lang;
    const ids = cur.f.evidence.event_ids;
    $('#lt-f').textContent = UI.depth === 'analyst'
      ? `${cur.tier} · ${checksPassed(cur)} · ev ${ids.slice(0, 4).join(',')}${ids.length > 4 ? '…' : ''}`
      : (UI.lang === 'es' ? '✓ Comprobado con los datos del partido' : '✓ Checked against match data');
  }
  lt.classList.toggle('show', Boolean(cur));
  renderOutputs(S.lastAired);
}

function renderOutputs(it) {
  const L = UI.lang.toUpperCase();
  $('#cap-h').textContent = `Caption · ${L}${UI.lang === 'es' ? ' · unreviewed' : ''}`;
  const badge = $('#cap-b');
  if (!it) {
    $('#cap-t').textContent = UI.lang === 'es' ? 'Pulsa play. Los datos emitidos aparecen aquí.' : 'Press play. Aired facts appear here.';
    badge.textContent = 'waiting';
    badge.className = 'tag c-muted';
    $('#aud-t').textContent = 'Numbers are spelled out for speech.';
    $('#aud-n').textContent = '';
    $('#cue-j').textContent = '// no cues yet';
    return;
  }
  const cap = rend(it.f, 'caption', UI.lang), audio = rend(it.f, 'audio_script', 'en');
  $('#cap-t').textContent = cap.text;
  $('#cap-t').lang = UI.lang;
  badge.textContent = cap.numbers_preserved ? 'numbers ✓' : 'numbers ✗';
  badge.className = 'tag ' + (cap.numbers_preserved ? 'c-green' : 'c-red');
  $('#aud-t').textContent = audio ? audio.text : '';
  $('#aud-n').textContent = UI.lang === 'es' ? 'Spanish audio script is not built yet (roadmap); showing English.' : '';
  const cue = CUES.find(c => c.fact_id === it.id && c.lang === UI.lang);
  $('#cue-j').textContent = cue ? JSON.stringify(cue, null, 1)
    : '// T2 fact approved live, so it is not in the pre-built cues.json\n' + JSON.stringify(rend(it.f, 'cue', UI.lang), null, 1);
}

const mentions = (it, pid) => it.players.includes(pid);

function rowHead(it, rt, fol) {
  const live = rt.phase === 'aired' && S.onAir && S.onAir.it === it;
  const ph = PHASE[live ? 'onair' : rt.phase] || ['', ''];
  let by = rt.phase === 'aired' && rt.by !== 'auto' ? ' · ' + rt.by : '';
  if (rt.superseded) by += ' · not shown on screen (newer fact)';
  let h = it.rejected ? '<span class="tag c-red">REJ</span>' : `<span class="tag ${TIER_CLS[it.tier]}">${it.tier}</span>`;
  h += `<span class="st ${ph[1]}">${ph[0]}${by}</span>`;
  if (it.status === 'corrected') h += '<span class="tag c-amber">corrected</span>';
  if (it.hedge !== 'none') h += `<span class="tag c-muted">${esc(it.hedge)}</span>`;
  if (UI.depth === 'analyst' && it.f.injected) h += '<span class="tag c-pink">injected</span>';
  if (fol) h += '<span class="tag c-green">following</span>';
  return `<div class="h">${h}<span class="t">${minute(it.t)}</span></div>`;
}

/* Who drafted a fact, from its build-time audit trail. */
function draftedBy(f) {
  const s = ((f.audit || {}).steps || []).filter(x => x.agent === 'Scout' && x.outcome === 'drafted').pop();
  return s ? `drafted by Scout · Foundry · ${s.model} · attempt ${s.attempt}` : 'drafted by rules';
}

function rowAnalyst(it) {
  const f = it.f, u = f.uncertainty;
  let m = `<span>${esc(it.id)}</span><span>${f.evidence.event_ids.length} ev</span><span>${esc(draftedBy(f))}</span>`;
  if (u && u.ci_method === 'wilson') m += `<span>n=${u.n} · CI ${Math.round(u.ci_low * 100)}–${Math.round(u.ci_high * 100)}%</span>`;
  if (f.impact) m += `<span>Δwp ${f.impact.delta >= 0 ? '+' : ''}${f.impact.delta.toFixed(2)} (model)</span>`;
  let h = `<div class="meta">${m}</div><div class="chk">` + f.verification.checks
    .map(c => `<span class="${c.passed ? '' : 'f'}" title="${esc(c.detail)}">${c.passed ? '✓' : '✗'} ${esc(c.name)}</span>`).join('') + '</div>';
  if (f.risk) h += `<p class="why">${esc(f.risk.reasons.join(' · '))}</p>`;
  return h;
}

function decisionButton(decision, id) {
  const label = decision[0].toUpperCase() + decision.slice(1);
  const cls = decision === 'approve' ? 'go' : 'no';
  return `<button type="button" class="btn xs ${cls}" data-act="${esc(decision)}" data-id="${id}" aria-label="${esc(label)} ${id}">${esc(label)}</button>`;
}

function rowActions(it) {
  const id = esc(it.id), open = actionable(it);
  let h = '<div class="acts">';
  if (open) h += producerDecisions(it).map(d => decisionButton(d, id)).join('');
  h += `<button type="button" class="btn xs" data-act="ev" data-id="${id}" aria-label="Evidence for ${id}">Evidence</button>`;
  if (open) h += `<span class="cd" data-cd="${id}"></span>`;
  return h + '</div>';
}

function row(it) {
  const rt = S.rt[it.id], an = UI.depth === 'analyst';
  const fol = Boolean(UI.follow) && mentions(it, UI.follow);
  const fresh = !S.seen.has(it.id);
  S.seen.add(it.id);
  let h = `<article class="row ${it.rejected ? 'R' : it.tier}${fol ? ' fol' : ''}${fresh ? ' fresh' : ''}">` + rowHead(it, rt, fol);
  if (it.rejected) {
    h += `<p class="dr">${esc(it.f.text.draft_en)}</p><p class="why">${esc(it.f.verification.reject_reason)}</p>`;
  } else {
    if (an && it.status === 'corrected') h += `<p class="dr">${esc(it.f.text.draft_en)}</p>`;
    h += `<p lang="${UI.lang}">${esc(rend(it.f, 'cue', UI.lang).text)}</p>`;
  }
  if (an) h += rowAnalyst(it);
  h += rowActions(it);
  if (actionable(it)) h += `<div class="tbar"><i data-bar="${esc(it.id)}"></i></div>`;
  return h + '</article>';
}

export function renderQueue() {
  const active = document.activeElement, key = active && active.dataset ? active.dataset : {};
  const shown = D.items.filter(it => S.rt[it.id]).filter(it => !UI.follow || mentions(it, UI.follow) || actionable(it)).reverse();
  // Facts needing a decision go first, the one closest to its deadline at the top.
  const urgent = shown.filter(actionable).sort((a, b) => timeLeft(a) - timeLeft(b));
  shown.splice(0, shown.length, ...urgent, ...shown.filter(it => !actionable(it)));
  $('#rows').innerHTML = shown.length ? shown.map(row).join('')
    : `<div class="empty">${S.next ? 'No facts name this player yet.' : 'Nothing drafted yet. Press play on the replay strip.'}</div>`;
  if (key.act && key.id) restoreFocus(key.act, key.id);
  const waiting = D.items.filter(actionable).length;
  $('#qhead').innerHTML = `<div><span class="lbl">Waiting</span><b class="${waiting ? 'c-amber' : ''}">${waiting}</b></div>` +
    `<div><span class="lbl">Aired</span><b class="c-green">${S.c.aired}</b></div>` +
    `<div><span class="lbl">Caught</span><b class="c-red">${S.c.rejected + S.c.corrected}</b></div>`;
}

function restoreFocus(act, id) {
  const sel = a => $(`#rows button[data-act="${a}"][data-id="${CSS.escape(id)}"]`);
  const el = sel(act) || sel('ev');
  if (el) el.focus();
}

export function renderTimers() {
  for (const it of D.items) {
    if (!actionable(it)) continue;
    const left = timeLeft(it), airs = tierRule(it).on_timeout.state !== 'expired';
    const bar = $(`[data-bar="${it.id}"]`), cd = $(`[data-cd="${it.id}"]`);
    if (bar) bar.style.transform = `scaleX(${Math.max(0, left / it.ttl)})`;
    if (cd) {
      cd.textContent = `${airs ? 'airs in' : 'expires in'} ${Math.max(0, Math.ceil(left))} s${S.playing || S.t >= D.end ? '' : ' (paused)'}`;
      cd.className = 'cd ' + (airs ? 'c-cyan' : 'c-amber');
    }
  }
}

/* ---------- evidence drawer ---------- */
function evidenceRow(e, f, id) {
  if (!e) return `<tr><td>${id}</td><td colspan="5"><span class="tag c-red">not in the event log</span></td></tr>`;
  const w = f.evidence.window || f.claim.window;
  let flag = '';
  if (e.t > f.created_t) flag = ' <span class="tag c-red">after claim time</span>';
  else if (w && (e.t < w.start_t || e.t > w.end_t)) flag = ' <span class="tag c-red">outside window</span>';
  return `<tr><td class="num">${e.id}</td><td class="num">${clock(e.t)}${flag}</td><td>${esc(e.type.replace('_', ' '))}</td>` +
    `<td>${esc(e.team)}</td><td>${esc(e.player)}</td><td>${esc((e.outcome || '').replace('_', ' '))}</td></tr>`;
}

export function openEvidence(id) {
  const it = D.items.find(x => x.id === id), f = it.f;
  $('#dr-t').textContent = 'Evidence · ' + id;
  const rows = f.evidence.event_ids.map(eid => evidenceRow(D.byId.get(eid), f, eid)).join('');
  const checks = f.verification.checks
    .map(c => `<p class="ck"><span class="${c.passed ? 'c-green' : 'c-red'}">${c.passed ? '✓' : '✗'} ${esc(c.name)}</span> <span class="c-muted">${esc(c.detail)}</span></p>`).join('');
  const corr = (f.verification.corrections || [])
    .map(c => `<p><span class="c-red" style="text-decoration:line-through">${esc(c.from)}</span><br>→ <span class="c-green">${esc(c.to)}</span><br><span class="c-muted">${esc(c.reason)}</span></p>`).join('');
  const claim = it.rejected ? f.text.draft_en : rend(f, 'cue', UI.lang).text;
  $('#dr-b').innerHTML = `<p class="claim">${esc(claim)}</p>` +
    `<p class="note">Claim made at ${clock(f.created_t)}. Status: ${esc(it.status)}${it.tier ? ', tier ' + it.tier : ''}.</p>${checks}` +
    `<table><caption class="sr-only">Cited events</caption><thead><tr><th>Event</th><th>Time</th><th>Type</th><th>Team</th><th>Player</th><th>Outcome</th></tr></thead><tbody>${rows}</tbody></table>` +
    (corr ? `<div class="ph" style="padding-left:0"><h2>Corrections</h2></div>${corr}` : '') +
    '<p class="note">All events are synthetic.</p>';
  $('#drawer').showModal();
  $('#dr-x').focus();
}
