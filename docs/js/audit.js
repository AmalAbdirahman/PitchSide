/* Audit view: decision log, flow counters, build metrics and the per-fact verification table. */

import { D, S, $, esc, clock } from './data.js';
import { PHASE } from './state.js';

const CHECK_ORDER = ['recompute', 'evidence_exists', 'entities', 'numbers_match', 'intent_stripped', 'sample_size', 'meaning_matches'];
const VERDICT = { verified: ['c-green', '✓'], corrected: ['c-amber', '✎ corrected'] };

export function renderLog() {
  $('#log').innerHTML = S.log.length
    ? S.log.map(l => `<li><time>${clock(l.t)}</time><span class="${l.cls}">${esc(l.text)}</span></li>`).join('')
    : '<li><span></span><span class="c-muted">Decisions appear here.</span></li>';
}

export function renderAudit() {
  const c = S.c;
  const tiles = [
    ['Events so far', S.evNext], ['Drafts', c.drafts], ['Verified clean', c.verified], ['Corrected by verifier', c.corrected],
    ['Rejected by verifier', c.rejected], ['Aired', c.aired], ['Vetoed or rejected by producer', c.vetoed + c.declined], ['Expired, never aired', c.expired],
  ];
  $('#flow').innerHTML = tiles.map(([k, v]) => `<div><b>${v}</b><span>${k}</span></div>`).join('');
  const m = D.metrics;
  $('#build').innerHTML = `Bundle build: <b>${m.drafts}</b> drafts, <b>${m.injected_drafts}</b> with injected errors, ` +
    `<b>${m.injected_not_verified}</b> of those rejected or corrected by the verifier. ` +
    `Renditions failing the number check: <b>${m.renditions_failed_number_check}</b>.` +
    (m.scout ? ` Scout drafts: <b>${m.scout.first_pass}</b> passed first time, <b>${m.scout.after_retry}</b> after a retry, ` +
      `<b>${m.scout.rejected}</b> still rejected, <b>${m.scout.rules}</b> fell back to rules.` : '');
  const list = D.items.filter(it => S.rt[it.id]);
  $('#vt tbody').innerHTML = list.length ? list.map(auditRow).join('')
    : '<tr><td colspan="13" class="c-muted">No drafts yet. Press play.</td></tr>';
}

/* What the Reader understood, in words, for the trace's tooltip. */
function readingText(r) {
  if (!r) return 'no reading';
  const w = r.window || {};
  const when = w.kind === 'unstated' ? 'no window' : w.kind.replace('_', ' ') + (w.minute != null ? ' ' + w.minute : '');
  return `${r.subject.type} ${r.subject.id}, ${r.metric || 'no listed metric'}, ${when}, value ${r.value}`;
}

/* Trace as HTML: verifier verdicts in colour (green ✓ verified, amber ✎ corrected, red ✗ otherwise). */
function agentTrace(f) {
  return ((f.audit || {}).steps || []).map(s => {
    if (s.agent === 'Verifier') {
      const [cls, mark] = VERDICT[s.outcome] || ['c-red', '✗'];
      return `Verifier <span class="${cls}">${mark}</span>`;
    }
    if (s.agent === 'Reader') return `<span title="Reader understood: ${esc(readingText(s.reading))}">Reader${s.outcome === 'read' ? '' : ' ' + esc(s.outcome)}</span>`;
    if (s.agent === 'Scout') return `Scout #${s.attempt}${s.outcome === 'drafted' ? '' : ' ' + esc(s.outcome.split(':')[0])}`;
    return 'Rules';
  }).join(' → ');
}

function auditRow(it) {
  const ph = PHASE[S.rt[it.id].phase] || ['…', ''];
  const byName = Object.fromEntries(it.f.verification.checks.map(c => [c.name, c]));
  const cells = CHECK_ORDER.map(n => {
    const c = byName[n];
    if (!c) return '<td class="ic c-muted">–</td>';
    return `<td class="ic ${c.passed ? 'c-green' : 'c-red'}" title="${esc(c.detail)}">${c.passed ? '✓' : '✗'}<span class="sr-only">${c.passed ? 'passed' : 'failed'}</span></td>`;
  }).join('');
  const st = it.status === 'verified' ? 'c-green' : it.status === 'corrected' ? 'c-amber' : 'c-red';
  return `<tr><td class="num">${clock(it.t)}</td><td><span style="font-family:var(--mono);font-size:11px">${esc(it.id)}</span>` +
    `${it.f.injected ? '<br><span class="tag c-pink">injected</span>' : ''}</td><td>${esc(it.f.text.draft_en)}</td>` +
    `<td class="trace">${agentTrace(it.f)}</td>${cells}` +
    `<td><span class="tag ${st}">${esc(it.status)}</span></td><td class="${ph[1]}">${ph[0]}</td></tr>`;
}
