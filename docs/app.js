/* Pitchside control room: replays docs/data/hero.json. No network beyond the two local data files. */
'use strict';

const $ = sel => document.querySelector(sel);
const $$ = sel => [...document.querySelectorAll(sel)];
const MATCH_S_PER_S = 40;  // at 1x, one match minute passes every 1.5 real seconds
const CHECK_ORDER = ['recompute', 'evidence_exists', 'entities', 'numbers_match', 'intent_stripped', 'sample_size'];
const PHASE = {
  veto: ['Veto window', 'c-cyan'], queued: ['Awaiting producer', 'c-amber'], aired: ['Aired', 'c-green'],
  vetoed: ['Vetoed', 'c-red'], onair: ['On air now', 'c-green'], declined: ['Rejected by producer', 'c-red'], expired: ['Expired · not aired', 'c-red'],
  rejected: ['Rejected by verifier', 'c-red'],
};
const TIER_CLS = { T0: 'c-green', T1: 'c-cyan', T2: 'c-amber' };
const LANES = ['left', 'centre', 'right'];
const HOME_COL = '#04f5ff', AWAY_COL = '#ff2d7e';

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
const teamCol = team => (team === D.meta.home ? HOME_COL : AWAY_COL);
const idxAt = t => Math.min(D.series.t.length - 1, Math.floor(t / D.series.step_s));
const fix = (v, d) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

/* Event coordinates are per team, attacking left to right. On screen the home side attacks right. */
function toScreen(team, x, y) {
  const p = D.meta.pitch;
  return team === D.meta.home ? [x, y] : [p.length - x, p.width - y];
}

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
  return { meta: hero.meta, metrics: hero.metrics, scores: hero.score_timeline, series: hero.series, events, items, players,
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
  $('#wp-name').textContent = home;
  drawPitch();
  drawRibbon();
  fillFollow();
  fillRules();
  setupSpeech();
}

function pitchLines(op) {
  const p = D.meta.pitch, L = p.length, W = p.width, my = W / 2;
  const s = `fill="none" stroke="rgba(255,255,255,${op})" stroke-width=".3"`;
  return `<rect x="0" y="0" width="${L}" height="${W}" ${s}/><line x1="${L / 2}" y1="0" x2="${L / 2}" y2="${W}" ${s}/>
    <circle cx="${L / 2}" cy="${my}" r="9.15" ${s}/><circle cx="${L / 2}" cy="${my}" r=".5" fill="rgba(255,255,255,${op})"/>
    <rect x="0" y="${my - 20.15}" width="16.5" height="40.3" ${s}/><rect x="${L - 16.5}" y="${my - 20.15}" width="16.5" height="40.3" ${s}/>
    <rect x="0" y="${my - 9.15}" width="5.5" height="18.3" ${s}/><rect x="${L - 5.5}" y="${my - 9.15}" width="5.5" height="18.3" ${s}/>
    <rect x="-1.6" y="${my - 3.66}" width="1.6" height="7.32" ${s}/><rect x="${L}" y="${my - 3.66}" width="1.6" height="7.32" ${s}/>`;
}

function drawPitch() {
  const p = D.meta.pitch, d = 'stroke="rgba(255,255,255,.12)" stroke-dasharray="1 1" stroke-width=".25"';
  const grid = [p.own_third_x, p.final_third_x].map(x => `<line x1="${x}" y1="0" x2="${x}" y2="${p.width}" ${d}/>`)
    .concat([1, 2].map(k => `<line x1="0" y1="${p.width * k / 3}" x2="${p.length}" y2="${p.width * k / 3}" ${d}/>`)).join('');
  $('#pitch').innerHTML = `<rect x="-2" y="-2" width="${p.length + 4}" height="${p.width + 4}" fill="#0d0617"/>
    <g id="zones"></g><g id="lanegrid" opacity="0">${grid}</g>${pitchLines(0.22)}
    <text x="${p.length - 1}" y="${p.width + 1.6}" text-anchor="end" font-size="1.6" font-weight="800" fill="${HOME_COL}">${esc(D.meta.home)} ATTACK ▶</text>
    <text x="1" y="${p.width + 1.6}" font-size="1.6" font-weight="800" fill="${AWAY_COL}">◀ ${esc(D.meta.away)} ATTACK</text>
    <g id="trail"></g><circle id="ring" r="2.6" fill="none" stroke="#00ff85" stroke-width=".35" stroke-dasharray="1 .8" opacity="0"/>
    <circle id="ball" r=".75" fill="#fff" opacity="0"/>
    <g id="tag" opacity="0"><rect id="tag-bg" height="3.4" fill="#38003c"/><rect width=".5" height="3.4" fill="#00ff85"/>
      <text id="tag-tx" x="1.3" y="2.35" font-size="1.8" font-weight="800" fill="#fff"></text></g>`;
}

function eventTag(e) {
  let label = `${e.player.toUpperCase()} · ${e.type.replace('_', ' ').toUpperCase()}`;
  if (UI.depth !== 'analyst') return label;
  if (e.type === 'shot') label += ` · ${e.shot_speed} KM/H · xG ${e.xg.toFixed(2)}`;
  else if (e.type === 'pass') label += ` · ${e.ball_speed} KM/H · DIFF ${e.pass_difficulty.toFixed(2)}`;
  else if (e.type === 'carry') label += ` · ${e.player_speed} KM/H`;
  return label;
}

function trailMarks(recent) {
  return recent.map((e, i) => {
    const op = ((i + 1) / recent.length).toFixed(2), col = teamCol(e.team), [x1, y1] = toScreen(e.team, e.x, e.y);
    let g = `<circle cx="${x1}" cy="${y1}" r=".8" fill="${col}" opacity="${op}"/>`;
    if ('end_x' in e) {
      const [x2, y2] = toScreen(e.team, e.end_x, e.end_y), dash = e.type === 'carry' ? ' stroke-dasharray=".8 .6"' : '';
      g += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${col}" stroke-width=".35" opacity="${op}"${dash}/>`;
    }
    return g;
  }).join('');
}

function setAttrs(sel, attrs) {
  const el = $(sel);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
}

function renderPitch() {
  const recent = D.events.slice(Math.max(0, S.evNext - 6), S.evNext).filter(e => 'x' in e);
  $('#trail').innerHTML = trailMarks(recent);
  $('#lanegrid').setAttribute('opacity', UI.depth === 'analyst' ? 1 : 0);
  for (const id of ['#ball', '#tag', '#ring']) $(id).setAttribute('opacity', 0);
  const cur = recent[recent.length - 1], p = D.meta.pitch;
  if (!cur) return;
  const [bx, by] = 'end_x' in cur ? toScreen(cur.team, cur.end_x, cur.end_y) : toScreen(cur.team, cur.x, cur.y);
  setAttrs('#ball', { cx: bx, cy: by, opacity: 1 });
  const label = eventTag(cur), w = label.length * 1.08 + 2.4;
  $('#tag-tx').textContent = label;
  $('#tag-bg').setAttribute('width', w);
  const tx = Math.min(Math.max(bx - w / 2, 0.5), p.length - w - 0.5), ty = by > 8 ? by - 6.5 : by + 3;
  setAttrs('#tag', { transform: `translate(${tx.toFixed(1)} ${ty.toFixed(1)})`, opacity: 1 });
  if (UI.follow && cur.player_id === UI.follow) setAttrs('#ring', { cx: bx, cy: by, opacity: 1 });
}

function zoneFor(team, lanes) {
  const p = D.meta.pitch, band = p.width / 3, k = LANES.indexOf(lanes.overload), home = team === D.meta.home;
  const x0 = home ? p.length / 2 : 0, y0 = home ? k * band : p.width - (k + 1) * band;
  const pct = Math.round(lanes[lanes.overload].share * 100), mins = D.series.lane_window_s / 60;
  const text = `${team} ${lanes.overload.toUpperCase()} OVERLOAD · ${pct}% · ${mins} MIN`;
  const ty = y0 + band / 2 + 0.6, tx = home ? p.length / 2 + 1.5 : p.length / 2 - 1.5;  // clear of the corner graphics
  return `<rect x="${x0}" y="${y0}" width="${p.length / 2}" height="${band}" fill="${teamCol(team)}" opacity=".13"/>` +
    `<text x="${tx}" y="${ty}" text-anchor="${home ? 'start' : 'end'}" font-size="1.7" font-weight="800" fill="${teamCol(team)}">${esc(text)}</text>`;
}

function renderZones() {
  const i = idxAt(S.t);
  $('#zones').innerHTML = [D.meta.home, D.meta.away]
    .map(team => (D.series.lanes[team][i].overload ? zoneFor(team, D.series.lanes[team][i]) : '')).join('');
}

const ribX = t => 8 + (t / D.end) * 884;
const ribY = p => 46 - p * 40;

function drawRibbon() {
  const colour = { goal: '#ffd23f', substitution: '#04f5ff', card: '#ff2d7e' };
  const short = { goal: 'GOAL', substitution: 'SUB', card: 'CARD' };
  const wp = D.series.win_prob.map((r, i) => `${i ? 'L' : 'M'}${ribX(D.series.t[i]).toFixed(1)} ${ribY(r[0]).toFixed(1)}`).join('');
  let g = '<defs><clipPath id="rc"><rect id="rcr" x="0" y="0" width="8" height="52"/></clipPath></defs>';
  g += `<line x1="8" x2="892" y1="${ribY(0.5)}" y2="${ribY(0.5)}" stroke="rgba(255,255,255,.14)" stroke-dasharray="2 3"/>`;
  g += `<text x="10" y="${ribY(0.5) - 2}" class="rl">50%</text><g clip-path="url(#rc)">`;
  D.events.filter(e => colour[e.type]).forEach((e, i) => {
    const x = ribX(e.t).toFixed(1), y = i % 2 ? 51 : 8;
    g += `<line x1="${x}" x2="${x}" y1="1" y2="51" stroke="${colour[e.type]}" stroke-width="1.2" stroke-dasharray="2 2" vector-effect="non-scaling-stroke"/>`;
    g += `<text x="${+x + 3}" y="${y}" class="rl" style="fill:${colour[e.type]}">${short[e.type]} ${minute(e.t)}</text>`;
  });
  g += `<path d="${wp}" fill="none" stroke="#f5f1f9" stroke-width="2" vector-effect="non-scaling-stroke"/></g>`;
  g += '<line id="head" x1="8" x2="8" y1="0" y2="52" stroke="#00ff85" stroke-width="2" vector-effect="non-scaling-stroke"/>';
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
  // One lower-third slot: if facts pile up, the newest waiting one replaces older waiting ones on screen.
  while (S.airQ.length > 1) {
    const old = S.airQ.shift();
    S.rt[old.id].superseded = true;
    log(`Lower third for ${old.id} skipped on screen: newer fact ${it.id} was waiting`);
  }
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
  S.onAir = { it, left: cue.duration_s / UI.speed };  // hold scales with replay speed so graphics keep pace
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
  if (idxAt(S.t) !== S.zoneIdx) {
    S.zoneIdx = idxAt(S.t);
    renderZones();
  }
  if (UI.view === 'an') maybeRenderAnalysis(now);
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
  renderPitch();
  renderZones();
  S.anKey = null;
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
  const recent = D.events.slice(Math.max(0, S.evNext - 4), S.evNext).reverse();  // newest first
  $('#feed').innerHTML = recent.map(e => `<li class="${esc(e.team)}">${esc(eventLine(e))}</li>`).join('');
  renderPitch();
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
  const live = rt.phase === 'aired' && S.onAir && S.onAir.it === it;
  const ph = PHASE[live ? 'onair' : rt.phase] || ['', ''];
  let by = rt.phase === 'aired' && rt.by !== 'system' && rt.by !== 'auto' ? ' · ' + rt.by : '';
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
function scoutStep(f) {
  return ((f.audit || {}).steps || []).filter(s => s.agent === 'Scout' && s.outcome === 'drafted').pop() || null;
}
function draftedBy(f) {
  const s = scoutStep(f);
  return s ? `drafted by Scout · Foundry · ${s.model} · attempt ${s.attempt}` : 'drafted by rules';
}
const VERDICT = { verified: ['c-green', '✓'], corrected: ['c-amber', '✎ corrected'] };
/* Trace as HTML: verifier verdicts in colour (green ✓ verified, amber ✎ corrected, red ✗ otherwise). */
function agentTrace(f) {
  return ((f.audit || {}).steps || []).map(s => {
    if (s.agent === 'Verifier') {
      const [cls, mark] = VERDICT[s.outcome] || ['c-red', '✗'];
      return `Verifier <span class="${cls}">${mark}</span>`;
    }
    if (s.agent === 'Scout') return `Scout #${s.attempt}${s.outcome === 'drafted' ? '' : ' ' + esc(s.outcome.split(':')[0])}`;
    return 'Rules';
  }).join(' → ');
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
  // Facts needing a decision go first, the one closest to its deadline at the top.
  const urgent = shown.filter(actionable).sort((a, b) => S.rt[a.id].left - S.rt[b.id].left);
  shown.splice(0, shown.length, ...urgent, ...shown.filter(it => !actionable(it)));
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
    `Renditions failing the number check: <b>${m.renditions_failed_number_check}</b>.` +
    (m.scout ? ` Scout drafts: <b>${m.scout.first_pass}</b> passed first time, <b>${m.scout.after_retry}</b> after a retry, ` +
      `<b>${m.scout.rejected}</b> still rejected, <b>${m.scout.rules}</b> fell back to rules.` : '');
  const list = D.items.filter(it => S.rt[it.id]);
  $('#vt tbody').innerHTML = list.length ? list.map(auditRow).join('')
    : '<tr><td colspan="12" class="c-muted">No drafts yet. Press play.</td></tr>';
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

/* ---------- analysis (draws bundle series; computes no statistics) ---------- */
let lastAnalysis = 0;
function maybeRenderAnalysis(now) {
  const key = `${idxAt(S.t)}|${S.evNext}|${UI.follow}`;
  if (key === S.anKey || now - lastAnalysis < 250) return;
  S.anKey = key;
  lastAnalysis = now;
  renderXgRace();
  renderShots();
  renderCompare();
  renderLanes();
}

function xgScale() {
  const finals = [D.meta.home, D.meta.away].map(tm => D.series.teams[tm].xg_sum.slice(-1)[0] || 0);
  return Math.max(0.5, Math.ceil(Math.max(...finals) * 2) / 2);
}

function renderXgRace() {
  const top = xgScale(), X = t => 44 + (t / D.end) * 760, Y = v => 200 - (v / top) * 180;
  const i = idxAt(S.t), ts = D.series.t;
  let g = '';
  for (let v = 0; v <= top + 1e-9; v += 0.5) {
    g += `<line x1="44" x2="804" y1="${Y(v)}" y2="${Y(v)}" stroke="rgba(255,255,255,.07)"/><text x="36" y="${Y(v) + 3}" text-anchor="end" class="rl" style="font-size:10px">${v.toFixed(1)}</text>`;
  }
  for (let m = 0; m * 60 <= D.end; m += 15) g += `<text x="${X(m * 60)}" y="218" text-anchor="middle" class="rl" style="font-size:10px">${m}'</text>`;
  const ht = D.events.find(e => e.period === 2);
  if (ht) g += `<line x1="${X(ht.t)}" x2="${X(ht.t)}" y1="16" y2="200" stroke="rgba(255,255,255,.14)" stroke-dasharray="3 3"/><text x="${X(ht.t) + 4}" y="26" class="rl" style="font-size:10px">2ND HALF</text>`;
  for (const team of [D.meta.home, D.meta.away]) {
    const col = D.series.teams[team].xg_sum;
    let d = `M${X(0)} ${Y(0)}`;
    for (let k = 1; k <= i; k++) d += ` H${X(ts[k]).toFixed(1)} V${Y(col[k] || 0).toFixed(1)}`;
    d += ` H${X(Math.min(S.t, D.end)).toFixed(1)}`;
    g += `<path d="${d}" fill="none" stroke="${teamCol(team)}" stroke-width="2.5" stroke-linejoin="round"/>`;
    g += `<text x="${X(Math.min(S.t, D.end)) + 6}" y="${Y(col[i] || 0) + 4}" fill="${teamCol(team)}" font-size="12" font-weight="800" font-family="ui-monospace,monospace">${team} ${fix(col[i] || 0, 2)}</text>`;
  }
  for (const e of D.events) {
    if (e.type !== 'goal' || e.t > S.t) continue;
    const y = Y(D.series.teams[e.team].xg_sum[Math.min(i, idxAt(e.t) + 1)] || 0);  // first sample after the goal
    g += `<line x1="${X(e.t)}" x2="${X(e.t)}" y1="44" y2="${y}" stroke="#ffd23f" stroke-dasharray="2 3"/>`;
    g += `<circle cx="${X(e.t)}" cy="${y}" r="6" fill="${teamCol(e.team)}" stroke="#07030d" stroke-width="2"/>`;
    g += `<text x="${X(e.t)}" y="38" text-anchor="middle" fill="#ffd23f" font-size="11" font-weight="800">GOAL ${esc(e.player.toUpperCase())} ${minute(e.t)}</text>`;
  }
  $('#xgrace').innerHTML = g;
}

function renderShots() {
  const p = D.meta.pitch;
  let g = `<rect x="0" y="0" width="${p.length}" height="${p.width}" rx="2" fill="#0a0412"/>${pitchLines(0.18)}`;
  for (const e of D.events) {
    if (e.type !== 'shot' || e.t > S.t) continue;
    const [x, y] = toScreen(e.team, e.x, e.y), col = teamCol(e.team), r = 0.8 + Math.sqrt(e.xg) * 4.2;
    const hi = UI.follow && e.player_id === UI.follow;
    g += `<circle cx="${x}" cy="${y}" r="${r.toFixed(2)}" fill="${col}" fill-opacity="${e.outcome === 'goal' ? 1 : 0.2}" stroke="${hi ? '#00ff85' : col}" stroke-width="${hi ? 0.7 : 0.3}">` +
      `<title>${esc(e.player)} ${minute(e.t)} · xG ${e.xg.toFixed(2)} · ${e.shot_speed} km/h · ${esc(e.outcome.replace('_', ' '))}</title></circle>`;
  }
  g += `<text x="${p.length - 1}" y="${p.width - 1.5}" text-anchor="end" font-size="2.2" fill="${HOME_COL}" font-weight="800">${esc(D.meta.home)} ATTACK ▶</text>`;
  g += `<text x="1" y="${p.width - 1.5}" font-size="2.2" fill="${AWAY_COL}" font-weight="800">◀ ${esc(D.meta.away)} ATTACK</text>`;
  $('#shotmap').innerHTML = g;
  $('#shot-note').textContent = UI.follow ? `${nameOf(UI.follow)}'s shots are outlined in green. Hover a shot for details.`
    : 'Pick a player in Follow to outline their shots. Hover a shot for details.';
}

function compareRow(name, a, b, digits) {
  const va = a || 0, vb = b || 0, sum = va + vb || 1;
  return `<div class="r"><span class="v c-cyan">${fix(a, digits)}</span><span class="n">${name}</span><span class="v c-pink">${fix(b, digits)}</span>` +
    `<div class="bars"><i style="width:${(va / sum) * 100}%;background:${HOME_COL}"></i><i style="width:${(vb / sum) * 100}%;background:${AWAY_COL}"></i></div></div>`;
}

function renderCompare() {
  const i = idxAt(S.t), h = D.series.teams[D.meta.home], a = D.series.teams[D.meta.away], wp = D.series.win_prob[i];
  const rows = [
    ['Win prob % (model estimate)', wp[0] * 100, wp[2] * 100, 0], ['Shots', h.shots[i], a.shots[i], 0],
    ['xG', h.xg_sum[i], a.xg_sum[i], 2], ['Pass accuracy %', h.pass_accuracy[i], a.pass_accuracy[i], 0],
    ['Turnovers in own third', h.turnovers_own_third[i], a.turnovers_own_third[i], 0],
    ['Top shot speed km/h', h.top_shot_speed[i], a.top_shot_speed[i], 1],
  ];
  $('#cmp').innerHTML = rows.map(r => compareRow(...r)).join('');
  $('#cmp-t').textContent = `${D.meta.home} vs ${D.meta.away} · to ${minute(S.t)} · draw ${Math.round(wp[1] * 100)}%`;
}

const HEDGE_TEXT = {
  early: ['EARLY', 'c-amber', 'Too few attacks to say much. Any caption must say "early sign".'],
  suggests: ['SUGGESTS', 'c-cyan', 'The interval overlaps the baseline. A caption must say "suggests".'],
  clear: ['CLEAR', 'c-green', 'The interval excludes the baseline. A plain statement is allowed.'],
};

function laneRow(name, lane, base, col) {
  const W = 600, X = v => v * W;
  const bracket = `<line x1="${X(lane.lo)}" x2="${X(lane.hi)}" y1="11" y2="11" stroke="#f5f1f9" stroke-width="2"/>` +
    `<line x1="${X(lane.lo)}" x2="${X(lane.lo)}" y1="5" y2="17" stroke="#f5f1f9" stroke-width="2"/><line x1="${X(lane.hi)}" x2="${X(lane.hi)}" y1="5" y2="17" stroke="#f5f1f9" stroke-width="2"/>`;
  return `<div class="lane"><span class="lbl">${name}</span>
    <svg viewBox="0 0 ${W} 22" preserveAspectRatio="none" aria-hidden="true"><rect x="0" y="6" width="${W}" height="10" fill="rgba(255,255,255,.06)" rx="5"/>
      <rect x="0" y="6" width="${X(lane.share)}" height="10" fill="${col}" rx="5"/>${bracket}
      <line x1="${X(base)}" x2="${X(base)}" y1="0" y2="22" stroke="#ffd23f" stroke-dasharray="3 2" stroke-width="1.5"/></svg>
    <span class="val">${lane.k} · ${Math.round(lane.share * 100)}% <span class="c-muted">[${Math.round(lane.lo * 100)}–${Math.round(lane.hi * 100)}]</span></span></div>`;
}

function lanePanel(team, i) {
  const L = D.series.lanes[team][i], base = D.series.lane_baseline;
  let h = `<h3 style="color:${teamCol(team)}">${esc(D.meta.teams[team].name)} · ${L.n} attacks</h3>`;
  if (!L.n) return h + '<p class="note">No attacks in the window yet.</p>';
  h += LANES.map(ln => laneRow(ln[0].toUpperCase() + ln.slice(1), L[ln], base, ln === L.top ? teamCol(team) : '#7d6c94')).join('');
  const [word, cls, why] = HEDGE_TEXT[L.hedge];
  const over = L.overload ? ` Flagged as a ${L.overload}-lane overload.` : '';
  return h + `<div class="hedge"><span class="tag ${cls}">${word}</span><span>Most attacks go ${L.top === 'centre' ? 'through the centre' : 'down the ' + L.top} (${Math.round(L[L.top].share * 100)}%). ${why}${over}</span></div>`;
}

function renderLanes() {
  const i = idxAt(S.t), mins = D.series.lane_window_s / 60;
  $('#lanes-t').textContent = `Last ${mins} min of play · 95% Wilson interval · dashed line = ${Math.round(D.series.lane_baseline * 100)}% baseline`;
  $('#lanes-h').innerHTML = lanePanel(D.meta.home, i);
  $('#lanes-a').innerHTML = lanePanel(D.meta.away, i);
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
  for (const k of ['bc', 'an', 'au']) $('#view-' + k).hidden = k !== v;
  S.anKey = null;
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
