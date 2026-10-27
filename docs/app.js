/* Pitchside control room: replays docs/data/hero.json. No network beyond the two local data files. */
'use strict';

const $ = sel => document.querySelector(sel);
const $$ = sel => [...document.querySelectorAll(sel)];
const MATCH_S_PER_S = 40;  // at 1x, one match minute passes every 1.5 real seconds
const CHECK_ORDER = ['recompute', 'evidence_exists', 'entities', 'numbers_match', 'intent_stripped', 'sample_size'];
const PHASE = {
  veto: ['Veto window', 'c-cyan'], queued: ['Awaiting producer', 'c-amber'], aired: ['On air', 'c-green'],
  vetoed: ['Vetoed', 'c-red'], declined: ['Rejected by producer', 'c-red'], expired: ['Expired · not aired', 'c-red'],
  rejected: ['Rejected by verifier', 'c-red'],
};
const TIER_CLS = { T0: 'c-green', T1: 'c-cyan', T2: 'c-amber' };

let D = null;      // bundle: meta, events, items, players, end
let CUES = [];     // docs/data/cues.json
let S = null;      // replay state
const UI = { lang: 'en', depth: 'casual', follow: '', auto: false, view: 'bc', speed: 1 };

/* ---------- helpers ---------- */
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pad = n => String(n).padStart(2, '0');
const clock = t => pad(Math.floor(t / 60)) + ':' + pad(Math.floor(t % 60));
const minute = t => Math.floor(t / 60) + 1 + "'";
const nameOf = id => (D.players[id] ? D.players[id].name : id || '');
const rend = (f, kind, lang) => f.renditions.find(r => r.kind === kind && r.lang === lang) || null;

function freshState() {
  return {
    t: 0, playing: false, next: 0, evNext: 0, rt: {}, seen: new Set(), log: [],
    airQ: [], onAir: null, lastAired: null, dirty: true, feedDirty: true,
    c: { drafts: 0, verified: 0, corrected: 0, rejected: 0, aired: 0, vetoed: 0, declined: 0, expired: 0 },
  };
}

/* ---------- loading ---------- */
async function getJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(path + ' returned ' + res.status);
  return res.json();
}

async function load() {
  try {
    const [hero, cues] = await Promise.all([getJson('data/hero.json'), getJson('data/cues.json')]);
    D = prepare(hero);
    CUES = cues;
  } catch (err) {
    const box = $('#load-error');
    box.hidden = false;
    box.innerHTML = '<b>Could not load the replay bundle.</b> ' + esc(err.message) +
      '<br>Serve the folder over HTTP, for example <code>python -m http.server 8000 --directory docs</code>, then open <code>localhost:8000</code> in a browser.';
    return;
  }
  S = freshState();
  setupStatic();
  wire();
  renderAll();
  requestAnimationFrame(frame);
}

function prepare(hero) {
  const players = {};
  for (const [team, info] of Object.entries(hero.meta.teams)) {
    for (const [id, name] of Object.entries(info.players)) players[id] = { name, team };
  }
  const items = hero.facts.map(f => toItem(f, false)).concat(hero.log.map(f => toItem(f, true)));
  items.sort((a, b) => a.t - b.t || a.id.localeCompare(b.id));
  const events = hero.events.slice().sort((a, b) => a.t - b.t);
  const last = Math.max(events[events.length - 1].t, items[items.length - 1].t);
  return { meta: hero.meta, metrics: hero.metrics, scores: hero.score_timeline, events, items, players,
    byId: new Map(hero.events.map(e => [e.id, e])), end: last };
}

function toItem(f, rejected) {
  const ids = new Set(f.evidence.players || []);
  if (f.claim.subject.type === 'player') ids.add(f.claim.subject.id);
  return {
    id: f.fact_id, f, rejected,
    t: rejected ? f.created_t : f.approval.valid_from_t,
    tier: rejected ? null : f.risk.tier,
    status: f.verification.status,
    hedge: f.uncertainty ? f.uncertainty.hedge : 'none',
    ttl: rejected ? 0 : f.approval.ttl_s || 0,
    players: [...ids],
  };
}

/* ---------- static parts ---------- */
function setupStatic() {
  const { home, away, teams } = D.meta;
  $('#home-name').textContent = teams[home].name.toUpperCase();
  $('#away-name').textContent = teams[away].name.toUpperCase();
  $('#home-ab').textContent = $('#bug-h').textContent = home;
  $('#away-ab').textContent = $('#bug-a').textContent = away;
  drawPitch();
  drawRibbon();
  fillFollow();
  fillRules();
  setupSpeech();
}

function drawPitch() {
  const s = 'fill="none" stroke="rgba(255,255,255,.22)" stroke-width=".3"';
  $('#pitch').innerHTML = `<rect x="-2" y="-2" width="109" height="72" fill="#0d0617"/>
    <rect x="0" y="0" width="105" height="68" ${s}/><line x1="52.5" y1="0" x2="52.5" y2="68" ${s}/>
    <circle cx="52.5" cy="34" r="9.15" ${s}/><circle cx="52.5" cy="34" r=".5" fill="rgba(255,255,255,.22)"/>
    <rect x="0" y="13.85" width="16.5" height="40.3" ${s}/><rect x="88.5" y="13.85" width="16.5" height="40.3" ${s}/>
    <rect x="0" y="24.85" width="5.5" height="18.3" ${s}/><rect x="99.5" y="24.85" width="5.5" height="18.3" ${s}/>
    <rect x="-1.6" y="30.34" width="1.6" height="7.32" ${s}/><rect x="105" y="30.34" width="1.6" height="7.32" ${s}/>`;
}

const ribX = t => 8 + (t / D.end) * 884;

function drawRibbon() {
  const colour = { goal: '#ffd23f', substitution: '#04f5ff', card: '#ff2d7e' };
  const short = { goal: 'GOAL', substitution: 'SUB', card: 'CARD' };
  let g = '<defs><clipPath id="rc"><rect id="rcr" x="0" y="0" width="8" height="56"/></clipPath></defs>';
  g += '<rect x="8" y="24" width="884" height="6" rx="3" fill="rgba(255,255,255,.08)"/>';
  g += '<rect x="8" y="24" width="884" height="6" rx="3" fill="#00ff85" clip-path="url(#rc)"/>';
  D.events.filter(e => colour[e.type]).forEach((e, i) => {
    const x = ribX(e.t).toFixed(1), y = i % 2 ? 50 : 14;
    g += `<line x1="${x}" x2="${x}" y1="6" y2="48" stroke="${colour[e.type]}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
    g += `<text x="${+x + 3}" y="${y}" class="rl" style="fill:${colour[e.type]}">${short[e.type]} ${minute(e.t)}</text>`;
  });
  g += '<line id="head" x1="8" x2="8" y1="0" y2="56" stroke="#f5f1f9" stroke-width="2" vector-effect="non-scaling-stroke"/>';
  $('#ribbon').innerHTML = g;
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

function fillRules() {
  const t1 = D.items.find(it => it.tier === 'T1'), t2 = D.items.find(it => it.tier === 'T2');
  if (t1) $('#rule-t1').textContent = `Airs after a ${t1.ttl} s veto window`;
  if (t2) $('#rule-t2').textContent = `Producer approves within ${t2.ttl} s or it is dropped`;
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

/* ---------- state machine ---------- */
function log(text, cls) {
  S.log.unshift({ t: S.t, text, cls: cls || '' });
  S.dirty = true;
}

const autoOk = it => it.tier === 'T2' && it.status === 'verified' && it.hedge === 'clear';

function admit(it) {
  S.c.drafts++;
  S.rt[it.id] = { phase: null, left: 0, by: null };
  if (it.rejected) {
    S.rt[it.id].phase = 'rejected';
    S.c.rejected++;
    log(`Verifier rejected ${it.id}: ${it.f.verification.reject_reason}`, 'c-red');
    return;
  }
  if (it.status === 'corrected') {
    S.c.corrected++;
    log(`Verifier corrected ${it.id}: ${it.f.verification.corrections.map(c => c.reason).join('; ')}`, 'c-amber');
  } else {
    S.c.verified++;
    log(`Verifier passed ${it.id}`, 'c-green');
  }
  route(it);
}

function route(it) {
  const rt = S.rt[it.id];
  if (it.tier === 'T0') return air(it, 'system');
  if (it.tier === 'T1') {
    Object.assign(rt, { phase: 'veto', left: it.ttl });
    return log(`${it.id} routed T1: ${it.ttl} s veto window`, 'c-cyan');
  }
  if (UI.auto && autoOk(it)) {
    log(`Autopilot approved ${it.id}`, 'c-green');
    return air(it, 'autopilot');
  }
  Object.assign(rt, { phase: 'queued', left: it.ttl });
  log(`${it.id} routed T2: waiting for producer, ${it.ttl} s`, 'c-amber');
}

function air(it, by) {
  Object.assign(S.rt[it.id], { phase: 'aired', by });
  S.c.aired++;
  S.airQ.push(it);
  log(`Aired ${it.id} (${by})`, 'c-green');
}

function decide(id, act) {
  const it = D.items.find(x => x.id === id), rt = S.rt[id];
  if (!rt) return;
  if (act === 'approve' && rt.phase === 'queued') {
    log(`Producer approved ${id}`, 'c-green');
    air(it, 'producer');
  } else if (act === 'reject' && rt.phase === 'queued') {
    rt.phase = 'declined';
    S.c.declined++;
    log(`Producer rejected ${id}; it will not air`, 'c-red');
  } else if (act === 'veto' && rt.phase === 'veto') {
    rt.phase = 'vetoed';
    S.c.vetoed++;
    log(`Producer vetoed ${id}; it will not air`, 'c-red');
  }
}

function tickTimers(dt) {
  for (const it of D.items) {
    const rt = S.rt[it.id];
    if (!rt || (rt.phase !== 'veto' && rt.phase !== 'queued')) continue;
    rt.left -= dt;
    if (rt.left > 0) continue;
    if (rt.phase === 'veto') {
      log(`Veto window closed for ${it.id}`);
      air(it, 'auto');
    } else {
      rt.phase = 'expired';
      S.c.expired++;
      log(`${it.id} expired unanswered and was never aired`, 'c-red');
    }
  }
}

function tickOnAir(dt) {
  if (S.onAir) {
    S.onAir.left -= dt;
    if (S.onAir.left > 0) return;
    S.onAir = null;
    S.dirty = true;
  }
  const it = S.airQ.shift();
  if (!it) return;
  const cue = rend(it.f, 'cue', 'en');
  S.onAir = { it, left: cue ? cue.duration_s : 6 };
  S.lastAired = it;
  if (it.f.kind === 'score_event') flash();
  S.dirty = true;
}

function advance(dt) {
  S.t = Math.min(D.end, S.t + dt * UI.speed * MATCH_S_PER_S);
  while (S.next < D.items.length && D.items[S.next].t <= S.t) admit(D.items[S.next++]);
  while (S.evNext < D.events.length && D.events[S.evNext].t <= S.t) {
    if (D.events[S.evNext].type === 'goal') popScore();
    S.evNext++;
    S.feedDirty = true;
  }
  if (S.t >= D.end) {
    S.playing = false;
    setPlayButton();
  }
}

/* ---------- loop ---------- */
let lastNow = performance.now();
function frame(now) {
  const dt = Math.min(0.25, Math.max(0, (now - lastNow) / 1000));  // rAF stamps can precede lastNow
  lastNow = now;
  if (S.playing) advance(dt);
  if (S.playing || S.t >= D.end) {
    tickTimers(dt);
    tickOnAir(dt);
  }
  if (S.dirty) renderAll();
  if (S.feedDirty) renderFeed();
  renderClock();
  renderTimers();
  requestAnimationFrame(frame);
}

/* ---------- rendering ---------- */
function renderAll() {
  S.dirty = false;
  renderQueue();
  renderOnAir();
  renderLog();
  renderAudit();
}

function scoreAt(t) {
  let s = D.scores[0];
  for (const x of D.scores) if (x.t <= t) s = x;
  return `${s[D.meta.home]}–${s[D.meta.away]}`;
}

function renderClock() {
  const sc = scoreAt(S.t);
  if ($('#score').textContent !== sc) $('#score').textContent = $('#bug-s').textContent = sc;
  $('#clock').textContent = $('#clk2').textContent = clock(S.t);
  $('#bug-m').textContent = minute(S.t);
  const x = ribX(S.t);
  $('#rcr').setAttribute('width', x);
  $('#head').setAttribute('x1', x);
  $('#head').setAttribute('x2', x);
}

function eventLine(e) {
  let who = e.player;
  if (e.type === 'substitution' && e.player_off) who += ' on for ' + nameOf(e.player_off);
  return `${clock(e.t)} ${e.type.replace('_', ' ').toUpperCase()} · ${who}${e.outcome ? ' · ' + e.outcome.replace('_', ' ') : ''}`;
}

function renderFeed() {
  S.feedDirty = false;
  const recent = D.events.slice(Math.max(0, S.evNext - 4), S.evNext).reverse();
  $('#feed').innerHTML = recent.map(e => `<li class="${esc(e.team)}">${esc(eventLine(e))}</li>`).join('');
}

function flash() {
  const el = $('#flash');
  el.classList.remove('go');
  void el.offsetWidth;
  el.classList.add('go');
}

function popScore() {
  for (const el of [$('#score'), $('#bug-s')]) {
    el.classList.remove('pop');
    void el.offsetWidth;
    el.classList.add('pop');
  }
}

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

function renderOnAir() {
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
const actionable = it => ['veto', 'queued'].includes(S.rt[it.id].phase);

function rowHead(it, rt, fol) {
  const ph = PHASE[rt.phase] || ['', ''];
  const by = rt.phase === 'aired' && rt.by !== 'system' && rt.by !== 'auto' ? ' · ' + rt.by : '';
  let h = it.rejected ? '<span class="tag c-red">REJ</span>' : `<span class="tag ${TIER_CLS[it.tier]}">${it.tier}</span>`;
  h += `<span class="st ${ph[1]}">${ph[0]}${by}</span>`;
  if (it.status === 'corrected') h += '<span class="tag c-amber">corrected</span>';
  if (it.hedge !== 'none') h += `<span class="tag c-muted">${esc(it.hedge)}</span>`;
  if (UI.depth === 'analyst' && it.f.injected) h += '<span class="tag c-pink">injected</span>';
  if (fol) h += '<span class="tag c-green">following</span>';
  return `<div class="h">${h}<span class="t">${minute(it.t)}</span></div>`;
}

function rowAnalyst(it) {
  const f = it.f, u = f.uncertainty;
  let m = `<span>${esc(it.id)}</span><span>${f.evidence.event_ids.length} ev</span>`;
  if (u && u.ci_method === 'wilson') m += `<span>n=${u.n} · CI ${Math.round(u.ci_low * 100)}–${Math.round(u.ci_high * 100)}%</span>`;
  if (f.impact) m += `<span>Δwp ${f.impact.delta >= 0 ? '+' : ''}${f.impact.delta.toFixed(2)} (model)</span>`;
  let h = `<div class="meta">${m}</div><div class="chk">` + f.verification.checks
    .map(c => `<span class="${c.passed ? '' : 'f'}" title="${esc(c.detail)}">${c.passed ? '✓' : '✗'} ${esc(c.name)}</span>`).join('') + '</div>';
  if (f.risk) h += `<p class="why">${esc(f.risk.reasons.join(' · '))}</p>`;
  return h;
}

function rowActions(it, rt) {
  const id = esc(it.id);
  let h = '<div class="acts">';
  if (rt.phase === 'queued') {
    h += `<button type="button" class="btn xs go" data-act="approve" data-id="${id}" aria-label="Approve ${id}">Approve</button>`;
    h += `<button type="button" class="btn xs no" data-act="reject" data-id="${id}" aria-label="Reject ${id}">Reject</button>`;
  }
  if (rt.phase === 'veto') h += `<button type="button" class="btn xs no" data-act="veto" data-id="${id}" aria-label="Veto ${id}">Veto</button>`;
  h += `<button type="button" class="btn xs" data-act="ev" data-id="${id}" aria-label="Evidence for ${id}">Evidence</button>`;
  if (actionable(it)) h += `<span class="cd" data-cd="${id}"></span>`;
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
  h += rowActions(it, rt);
  if (actionable(it)) h += `<div class="tbar"><i data-bar="${esc(it.id)}"></i></div>`;
  return h + '</article>';
}

function renderQueue() {
  const active = document.activeElement, key = active && active.dataset ? active.dataset : {};
  const shown = D.items.filter(it => S.rt[it.id]).filter(it => !UI.follow || mentions(it, UI.follow) || actionable(it)).reverse();
  $('#rows').innerHTML = shown.length ? shown.map(row).join('')
    : `<div class="empty">${S.next ? 'No facts name this player yet.' : 'Nothing drafted yet. Press play on the replay strip.'}</div>`;
  if (key.act && key.id) restoreFocus(key.act, key.id);
  const waiting = D.items.filter(it => S.rt[it.id] && actionable(it)).length;
  $('#qhead').innerHTML = `<div><span class="lbl">Waiting</span><b class="${waiting ? 'c-amber' : ''}">${waiting}</b></div>` +
    `<div><span class="lbl">Aired</span><b class="c-green">${S.c.aired}</b></div>` +
    `<div><span class="lbl">Caught</span><b class="c-red">${S.c.rejected + S.c.corrected}</b></div>`;
}

function restoreFocus(act, id) {
  const sel = a => $(`#rows button[data-act="${a}"][data-id="${CSS.escape(id)}"]`);
  const el = sel(act) || sel('ev');
  if (el) el.focus();
}

function renderTimers() {
  for (const it of D.items) {
    const rt = S.rt[it.id];
    if (!rt || !actionable(it)) continue;
    const bar = $(`[data-bar="${it.id}"]`), cd = $(`[data-cd="${it.id}"]`);
    if (bar) bar.style.transform = `scaleX(${Math.max(0, rt.left / it.ttl)})`;
    if (cd) {
      const s = Math.max(0, Math.ceil(rt.left));
      cd.textContent = `${rt.phase === 'veto' ? 'airs in' : 'expires in'} ${s} s${S.playing || S.t >= D.end ? '' : ' (paused)'}`;
      cd.className = 'cd ' + (rt.phase === 'veto' ? 'c-cyan' : 'c-amber');
    }
  }
}

function renderLog() {
  $('#log').innerHTML = S.log.length
    ? S.log.map(l => `<li><time>${clock(l.t)}</time><span class="${l.cls}">${esc(l.text)}</span></li>`).join('')
    : '<li><span></span><span class="c-muted">Decisions appear here.</span></li>';
}

function renderAudit() {
  const c = S.c;
  const tiles = [
    ['Events so far', S.evNext], ['Drafts', c.drafts], ['Verified clean', c.verified], ['Corrected by verifier', c.corrected],
    ['Rejected by verifier', c.rejected], ['Aired', c.aired], ['Vetoed or rejected by producer', c.vetoed + c.declined], ['Expired, never aired', c.expired],
  ];
  $('#flow').innerHTML = tiles.map(([k, v]) => `<div><b>${v}</b><span>${k}</span></div>`).join('');
  const m = D.metrics;
  $('#build').innerHTML = `Bundle build: <b>${m.drafts}</b> drafts, <b>${m.injected_drafts}</b> with injected errors, ` +
    `<b>${m.injected_not_verified}</b> of those rejected or corrected by the verifier. ` +
    `Renditions failing the number check: <b>${m.renditions_failed_number_check}</b>.`;
  const list = D.items.filter(it => S.rt[it.id]);
  $('#vt tbody').innerHTML = list.length ? list.map(auditRow).join('')
    : '<tr><td colspan="11" class="c-muted">No drafts yet. Press play.</td></tr>';
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
    `${it.f.injected ? '<br><span class="tag c-pink">injected</span>' : ''}</td><td>${esc(it.f.text.draft_en)}</td>${cells}` +
    `<td><span class="tag ${st}">${esc(it.status)}</span></td><td class="${ph[1]}">${ph[0]}</td></tr>`;
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

function openEvidence(id) {
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

/* ---------- controls ---------- */
function setPlayButton() {
  const b = $('#play');
  b.textContent = S.playing ? '❚❚' : '▶';
  b.setAttribute('aria-label', S.playing ? 'Pause' : 'Play');
}

function restart() {
  S = freshState();
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
  $('#view-bc').hidden = v !== 'bc';
  $('#view-au').hidden = v !== 'au';
}

function oldest(phase) {
  return D.items.find(it => S.rt[it.id] && S.rt[it.id].phase === phase);
}

function act(id, what) {
  decide(id, what);
  renderAll();
}

function onKey(e) {
  if (e.ctrlKey || e.metaKey || e.altKey || /SELECT|INPUT|TEXTAREA/.test(e.target.tagName)) return;
  if ($('#drawer').open || $('#aboutdlg').open) return;
  const k = e.key.toLowerCase();
  if (e.code === 'Space' && e.target.tagName !== 'BUTTON' && e.target.tagName !== 'A') { e.preventDefault(); togglePlay(); }
  else if (k === 'a' || k === 'r') { const it = oldest('queued'); if (it) act(it.id, k === 'a' ? 'approve' : 'reject'); }
  else if (k === 'v') { const it = oldest('veto'); if (it) act(it.id, 'veto'); }
  else if (k === '1') setView('bc');
  else if (k === '2') setView('au');
}

function press(sel, fn) {
  $$(sel).forEach(b => b.addEventListener('click', () => {
    $$(sel).forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    fn(b);
    S.dirty = true;
  }));
}

function onAutopilot(e) {
  UI.auto = e.target.checked;
  log(`Autopilot ${UI.auto ? 'on' : 'off'}`);
  if (!UI.auto) return;
  for (const it of D.items) {
    if (S.rt[it.id] && S.rt[it.id].phase === 'queued' && autoOk(it)) {
      log(`Autopilot approved ${it.id}`, 'c-green');
      air(it, 'autopilot');
    }
  }
}

function speak() {
  const text = $('#aud-t').textContent, voice = localVoice('en');
  if (!voice || !text) return;
  const u = new SpeechSynthesisUtterance(text);
  u.voice = voice;
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
}

function wire() {
  $('#play').addEventListener('click', togglePlay);
  $('#restart').addEventListener('click', () => { restart(); S.playing = true; setPlayButton(); });
  press('[data-speed]', b => { UI.speed = Number(b.dataset.speed); });
  press('[data-depth]', b => { UI.depth = b.dataset.depth; document.body.dataset.mode = UI.depth; });
  press('[data-lang]', b => { UI.lang = b.dataset.lang; });
  $$('nav.views button').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));
  $('#follow').addEventListener('change', e => { UI.follow = e.target.value; S.dirty = true; });
  $('#auto').addEventListener('change', onAutopilot);
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
