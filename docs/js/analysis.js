/* Analysis view. Draws the series the bundle carries; computes no statistics. */

import { D, S, UI, $, esc, minute, fix, nameOf, teamCol, idxAt, toScreen, HOME_COL, AWAY_COL, LANES } from './data.js';
import { pitchLines } from './pitch.js';

let lastAnalysis = 0;
export function maybeRenderAnalysis(now) {
  const key = `${idxAt(S.t)}|${S.evNext}|${UI.follow}`;
  if (key === S.anKey || now - lastAnalysis < 250) return;
  S.anKey = key;
  lastAnalysis = now;
  renderXgRace();
  renderShots();
  renderCompare();
  renderLanes();
  renderThreat();
  renderNetwork();
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
    ['xT added', h.xt_added[i], a.xt_added[i], 2],
    ['Turnovers in own third', h.turnovers_own_third[i], a.turnovers_own_third[i], 0],
    ['Top shot speed km/h', h.top_shot_speed[i], a.top_shot_speed[i], 1],
  ];
  $('#cmp').innerHTML = rows.map(r => compareRow(...r)).join('');
  $('#cmp-t').textContent = `${D.meta.home} vs ${D.meta.away} · to ${minute(S.t)} · draw ${Math.round(wp[1] * 100)}%`;
}

const HEDGE_TEXT = {
  early: ['EARLY', 'c-amber', 'Too few attacks to say much. Any caption must say "early sign".'],
  suggests: ['SUGGESTS', 'c-cyan', 'Not yet clear of the league share once clustered attacks are allowed for. A caption must say "suggests".'],
  clear: ['CLEAR', 'c-green', 'Clearly above the league share once clustered attacks are allowed for. A plain statement is allowed.'],
};

function laneRow(name, lane, base, col) {
  const W = 600, X = v => v * W;
  const bracket = `<line x1="${X(lane.lo)}" x2="${X(lane.hi)}" y1="11" y2="11" stroke="#f5f1f9" stroke-width="2"/>` +
    `<line x1="${X(lane.lo)}" x2="${X(lane.lo)}" y1="5" y2="17" stroke="#f5f1f9" stroke-width="2"/><line x1="${X(lane.hi)}" x2="${X(lane.hi)}" y1="5" y2="17" stroke="#f5f1f9" stroke-width="2"/>`;
  return `<div class="lane"><span class="lbl">${name}</span>
    <svg viewBox="0 0 ${W} 22" preserveAspectRatio="none" aria-hidden="true"><rect x="0" y="6" width="${W}" height="10" fill="rgba(255,255,255,.06)" rx="5"/>
      <rect x="0" y="6" width="${X(lane.share)}" height="10" fill="${col}" rx="5"/>${bracket}
      <line x1="${X(base)}" x2="${X(base)}" y1="0" y2="22" stroke="#ffd23f" stroke-dasharray="3 2" stroke-width="1.5"/></svg>
    <span class="val">${lane.k} · ${Math.round(lane.share * 100)}% <span class="c-muted">[${Math.round(lane.lo * 100)}–${Math.round(lane.hi * 100)}] · P ${Math.round(lane.p_above * 100)}%</span></span></div>`;
}

function lanePanel(team, i) {
  const L = D.series.lanes[team][i], prior = D.series.lane_prior;
  let h = `<h3 style="color:${teamCol(team)}">${esc(D.meta.teams[team].name)} · ${L.n} attacks</h3>`;
  if (!L.n) return h + '<p class="note">No attacks in the window yet.</p>';
  h += LANES.map(ln => laneRow(ln[0].toUpperCase() + ln.slice(1), L[ln], prior[ln], ln === L.top ? teamCol(team) : '#7d6c94')).join('');
  const [word, cls, why] = HEDGE_TEXT[L.hedge];
  const over = L.overload ? ` Flagged as a ${L.overload}-lane overload.` : '';
  const top = L[L.top], p = Math.round(top.p_above * 100), lg = Math.round(prior[L.top] * 100);
  return h + `<div class="hedge"><span class="tag ${cls}">${word}</span><span>Most attacks go ${L.top === 'centre' ? 'through the centre' : 'down the ' + L.top} (${Math.round(top.share * 100)}%; league ${lg}%; P(above league) ${p}%). ${why}${over}</span></div>`;
}

function renderLanes() {
  const i = idxAt(S.t), mins = D.series.lane_window_s / 60;
  const clear = Math.round(D.series.hedge_clear_prob * 100);
  $('#lanes-t').textContent = `Last ${mins} min of play · bracket = 95% Wilson (raw counts, for comparison) · P = Beta-Binomial P(share > league), clear at ${clear}% · dashed line = league share`;
  $('#lanes-h').innerHTML = lanePanel(D.meta.home, i);
  $('#lanes-a').innerHTML = lanePanel(D.meta.away, i);
}

/* Threat map: the fitted xT surface (from the bundle) with the team's biggest completed moves so far.
   Each team is drawn attacking left to right, the way the surface is defined. */
const TOP_MOVES = 5;

function threatPitch(team) {
  const p = D.meta.pitch, X = D.series.xt, cw = p.length / X.cols, ch = p.width / X.rows;
  const top = Math.max(...X.surface.flat()), col = teamCol(team);
  let g = `<rect x="0" y="0" width="${p.length}" height="${p.width}" fill="#0a0412"/>`;
  X.surface.forEach((row, r) => row.forEach((v, c) => {
    g += `<rect x="${(c * cw).toFixed(2)}" y="${(r * ch).toFixed(2)}" width="${cw.toFixed(2)}" height="${ch.toFixed(2)}" fill="${col}" fill-opacity="${(Math.sqrt(v / top) * 0.7).toFixed(3)}"><title>xT ${v.toFixed(3)}</title></rect>`;
  }));
  g += pitchLines(0.25);
  const moves = X.moves[team].filter(([id]) => D.byId.get(id).t <= S.t)
    .sort((a, b) => b[1] - a[1]).slice(0, TOP_MOVES);
  moves.forEach(([id, gain], k) => {
    const e = D.byId.get(id), ink = UI.follow && e.player_id === UI.follow ? '#00ff85' : '#f5f1f9';
    g += `<g><title>${esc(nameOf(e.player_id))} ${minute(e.t)} · ${esc(e.type)} · xT +${gain.toFixed(3)}</title>` +
      `<line x1="${e.x}" y1="${e.y}" x2="${e.end_x}" y2="${e.end_y}" stroke="${ink}" stroke-width=".6"/>` +
      `<circle cx="${e.end_x}" cy="${e.end_y}" r="1.6" fill="#07030d" stroke="${ink}" stroke-width=".4"/>` +
      `<text x="${e.end_x}" y="${e.end_y + 0.7}" text-anchor="middle" font-size="2" font-weight="800" fill="#f5f1f9">${k + 1}</text></g>`;
  });
  g += `<text x="${p.length - 1}" y="${p.width - 1.5}" text-anchor="end" font-size="2.4" font-weight="800" fill="${col}">${esc(team)} ATTACK ▶</text>`;
  const list = moves.length ? moves.map(([id, gain], k) => {
    const e = D.byId.get(id);
    return `<li>${k + 1}. ${esc(nameOf(e.player_id))} · ${minute(e.t)} · ${esc(e.type)} · <b class="num">+${gain.toFixed(3)}</b></li>`;
  }).join('') : '<li>No completed passes or carries yet.</li>';
  const name = esc(D.meta.teams[team].name);
  return `<h3 style="color:${col}">${name}</h3>` +
    `<svg viewBox="-2 -2 109 72" role="img" aria-label="${name}: xT surface and the ${TOP_MOVES} largest xT gains so far">${g}</svg>` +
    `<ol class="xtlist">${list}</ol>`;
}

function renderThreat() {
  $('#xt-h').innerHTML = threatPitch(D.meta.home);
  $('#xt-a').innerHTML = threatPitch(D.meta.away);
}

/* Pass network: the bundle's network for the current half at the last 5-minute sample before the
   replay clock. Positions, degree, betweenness and groups all come from the bundle. */
const GROUP_COLS = ['#ffd23f', '#00ff85', '#b28cff', '#ff8a3d', '#7fd1ff'];

function netAt() {
  const N = D.series.network;
  const i = Math.max(0, Math.min(N.t.length - 1, Math.floor(S.t / N.step_s)));
  return { N, i, t: N.t[i] };
}

function networkPitch(team, snap) {
  const p = D.meta.pitch, name = esc(D.meta.teams[team].name), col = teamCol(team);
  if (!snap.nodes.length) return `<h3 style="color:${col}">${name}</h3><p class="note">No linked passes in this half yet.</p>`;
  const at = Object.fromEntries(snap.nodes.map(n => [n[0], n]));
  const pair = {};
  snap.edges.forEach(([a, b, k]) => { const key = a < b ? a + '|' + b : b + '|' + a; pair[key] = (pair[key] || 0) + k; });
  const top = snap.nodes.reduce((best, n) => (n[4] > best[4] ? n : best));
  const unique = snap.nodes.filter(n => n[4] === top[4]).length === 1;
  let g = `<rect x="0" y="0" width="${p.length}" height="${p.width}" fill="#0a0412"/>` + pitchLines(0.25);
  Object.entries(pair).forEach(([key, k]) => {
    const [a, b] = key.split('|').map(id => at[id]);
    g += `<line x1="${a[1]}" y1="${a[2]}" x2="${b[1]}" y2="${b[2]}" stroke="#f5f1f9" stroke-opacity="${Math.min(0.85, 0.2 + 0.12 * k).toFixed(2)}" stroke-width="${(0.25 + 0.2 * k).toFixed(2)}"><title>${esc(nameOf(a[0]))} – ${esc(nameOf(b[0]))}: ${k} passes</title></line>`;
  });
  // Larger circles first, so smaller ones stay visible on top; the most central player last.
  const order = [...snap.nodes].sort((a, b) => (unique && a[0] === top[0]) - (unique && b[0] === top[0]) || b[3] - a[3]);
  order.forEach(([id, x, y, deg, btw, grp]) => {
    const r = 1.1 + Math.sqrt(deg) * 0.4, ring = unique && id === top[0];
    const ink = UI.follow && id === UI.follow ? '#00ff85' : '#07030d';
    g += `<g><title>${esc(nameOf(id))}: ${deg} passes made or received · betweenness ${btw.toFixed(2)}</title>` +
      `<circle cx="${x}" cy="${y}" r="${r.toFixed(2)}" fill="${GROUP_COLS[grp % GROUP_COLS.length]}" stroke="${ring ? '#ffffff' : ink}" stroke-width="${ring ? 0.8 : 0.4}"/>` +
      `<text x="${x}" y="${(y + 0.8).toFixed(2)}" text-anchor="middle" font-size="2.2" font-weight="800" fill="#07030d">${esc(id.split('_').pop())}</text></g>`;
  });
  g += `<text x="${p.length - 1}" y="${p.width - 1.5}" text-anchor="end" font-size="2.4" font-weight="800" fill="${col}">${esc(team)} ATTACK ▶</text>`;
  const ranked = [...snap.nodes].sort((a, b) => b[4] - a[4]).slice(0, 3)
    .map(n => `${esc(nameOf(n[0]))} <b class="num">${n[4].toFixed(2)}</b>`).join(' · ');
  const lead = unique ? `Most central: <b>${esc(nameOf(top[0]))}</b>.` : 'No single most central player (tie).';
  return `<h3 style="color:${col}">${name} · ${snap.n} linked passes</h3>` +
    `<svg viewBox="-2 -2 109 72" role="img" aria-label="${name}: pass network for this half, ${snap.n} linked passes">${g}</svg>` +
    `<p class="netlist">${lead} Betweenness: ${ranked}</p>`;
}

function renderNetwork() {
  const { N, i, t } = netAt();
  const half = t < N.half_t ? 'First half' : 'Second half';
  $('#net-t').textContent = `${half} · to ${minute(t)} · sampled every ${N.step_s / 60} min · receivers inferred`;
  $('#net-h').innerHTML = networkPitch(D.meta.home, N.teams[D.meta.home][i]);
  $('#net-a').innerHTML = networkPitch(D.meta.away, N.teams[D.meta.away][i]);
}
