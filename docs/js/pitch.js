/* Broadcast view: pitch, ball trail, lane overload zones, event feed, clock and win-probability ribbon. */

import { D, S, UI, $, esc, clock, minute, nameOf, teamCol, teamInk, teamSide, idxAt, toScreen, LANES, COL } from './data.js';

export function pitchLines(op) {
  const p = D.meta.pitch, L = p.length, W = p.width, my = W / 2;
  const s = `fill="none" stroke="${COL.pitchLine}" stroke-opacity="${op}" stroke-width=".3"`;
  return `<rect x="0" y="0" width="${L}" height="${W}" ${s}/><line x1="${L / 2}" y1="0" x2="${L / 2}" y2="${W}" ${s}/>
    <circle cx="${L / 2}" cy="${my}" r="9.15" ${s}/><circle cx="${L / 2}" cy="${my}" r=".5" fill="${COL.pitchLine}" fill-opacity="${op}"/>
    <rect x="0" y="${my - 20.15}" width="16.5" height="40.3" ${s}/><rect x="${L - 16.5}" y="${my - 20.15}" width="16.5" height="40.3" ${s}/>
    <rect x="0" y="${my - 9.15}" width="5.5" height="18.3" ${s}/><rect x="${L - 5.5}" y="${my - 9.15}" width="5.5" height="18.3" ${s}/>
    <rect x="-1.6" y="${my - 3.66}" width="1.6" height="7.32" ${s}/><rect x="${L}" y="${my - 3.66}" width="1.6" height="7.32" ${s}/>`;
}

export function drawPitch() {
  const p = D.meta.pitch, d = `stroke="${COL.pitchLine}" stroke-opacity=".12" stroke-dasharray="1 1" stroke-width=".25"`;
  const grid = [p.own_third_x, p.final_third_x].map(x => `<line x1="${x}" y1="0" x2="${x}" y2="${p.width}" ${d}/>`)
    .concat([1, 2].map(k => `<line x1="0" y1="${p.width * k / 3}" x2="${p.length}" y2="${p.width * k / 3}" ${d}/>`)).join('');
  $('#pitch').innerHTML = `<rect x="-2" y="-2" width="${p.length + 4}" height="${p.width + 4}" fill="${COL.pitch}"/>
    <g id="zones"></g><g id="lanegrid" opacity="0">${grid}</g>${pitchLines(0.22)}
    <text x="${p.length - 1}" y="${p.width + 1.6}" text-anchor="end" font-size="1.6" font-weight="800" fill="${COL.homeText}">${esc(D.meta.home)} ATTACK ▶</text>
    <text x="1" y="${p.width + 1.6}" font-size="1.6" font-weight="800" fill="${COL.awayText}">◀ ${esc(D.meta.away)} ATTACK</text>
    <g id="trail"></g><circle id="ring" r="2.6" fill="none" stroke="${COL.accent}" stroke-width=".35" stroke-dasharray="1 .8" opacity="0"/>
    <circle id="ball" r=".75" fill="${COL.text}" opacity="0"/>
    <g id="tag" opacity="0"><rect id="tag-bg" height="3.4" fill="${COL.airBg}"/><rect width=".5" height="3.4" fill="${COL.airAccent}"/>
      <text id="tag-tx" x="1.3" y="2.35" font-size="1.8" font-weight="800" fill="${COL.airText}"></text></g>`;
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

export function renderPitch() {
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
    `<text x="${tx}" y="${ty}" text-anchor="${home ? 'start' : 'end'}" font-size="1.7" font-weight="800" fill="${teamInk(team)}">${esc(text)}</text>`;
}

export function renderZones() {
  const i = idxAt(S.t);
  $('#zones').innerHTML = [D.meta.home, D.meta.away]
    .map(team => (D.series.lanes[team][i].overload ? zoneFor(team, D.series.lanes[team][i]) : '')).join('');
}

const ribX = t => 8 + (t / D.end) * 884;
const ribY = p => 46 - p * 40;

export function drawRibbon() {
  /* Goals in the scorer's team colour; the status colours stay for verifier outcomes. */
  const colour = e => ({ goal: teamCol(e.team), substitution: COL.muted, card: COL.text }[e.type]);
  const ink = e => (e.type === 'goal' ? teamInk(e.team) : colour(e));
  const short = { goal: 'GOAL', substitution: 'SUB', card: 'CARD' };
  const wp = D.series.win_prob.map((r, i) => `${i ? 'L' : 'M'}${ribX(D.series.t[i]).toFixed(1)} ${ribY(r[0]).toFixed(1)}`).join('');
  let g = '<defs><clipPath id="rc"><rect id="rcr" x="0" y="0" width="8" height="52"/></clipPath></defs>';
  g += `<line x1="8" x2="892" y1="${ribY(0.5)}" y2="${ribY(0.5)}" stroke="${COL.line2}" stroke-dasharray="2 3"/>`;
  g += `<text x="10" y="${ribY(0.5) - 2}" class="rl">50%</text><g clip-path="url(#rc)">`;
  D.events.filter(e => short[e.type]).forEach((e, i) => {
    const x = ribX(e.t).toFixed(1), y = i % 2 ? 51 : 8;
    g += `<line x1="${x}" x2="${x}" y1="1" y2="51" stroke="${colour(e)}" stroke-width="1.2" stroke-dasharray="2 2" vector-effect="non-scaling-stroke"/>`;
    g += `<text x="${+x + 3}" y="${y}" class="rl" style="fill:${ink(e)}">${short[e.type]} ${minute(e.t)}</text>`;
  });
  g += `<path d="${wp}" fill="none" stroke="${COL.text}" stroke-width="2" vector-effect="non-scaling-stroke"/></g>`;
  g += `<line id="head" x1="8" x2="8" y1="0" y2="52" stroke="${COL.accent}" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
  $('#ribbon').innerHTML = g;
}

function scoreAt(t) {
  let s = D.scores[0];
  for (const x of D.scores) if (x.t <= t) s = x;
  return `${s[D.meta.home]}–${s[D.meta.away]}`;
}

export function renderClock() {
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

export function renderFeed() {
  S.feedDirty = false;
  const recent = D.events.slice(Math.max(0, S.evNext - 4), S.evNext).reverse();  // newest first
  $('#feed').innerHTML = recent.map(e => `<li class="${teamSide(e.team)}">${esc(eventLine(e))}</li>`).join('');
  renderPitch();
}

export function flash() {
  const el = $('#flash');
  el.classList.remove('go');
  void el.offsetWidth;
  el.classList.add('go');
}

export function popScore() {
  for (const el of [$('#score'), $('#bug-s')]) {
    el.classList.remove('pop');
    void el.offsetWidth;
    el.classList.add('pop');
  }
}
