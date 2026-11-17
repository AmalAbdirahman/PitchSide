/* R3 match stats: npxG by 15-minute interval, shot outcomes and result probability.
   Pure functions from the bundle's series to markup, so they run under node --test. They only
   draw numbers from hero.json up to series index i (the replay clock); they compute none.
   col holds the colour tokens (data.js COL): team colours for marks, neutrals for the rest.
   Green, amber and red mean verifier outcomes only, so shot outcomes do not use them. */

const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const n2 = v => Number(v).toFixed(2);

/* ---------- npxG by interval: grouped bars ---------- */

/* Each interval's npxG for one team at index i; null for intervals that have not started. */
export function intervalValues(series, team, i) {
  const row = series.teams[team].npxg_by_interval[i] || [];
  return series.intervals.map((_, k) => (k < row.length ? row[k] : null));
}

/* One fixed scale for the whole match, so bars do not jump as the replay runs. */
function intervalTop(series, home, away) {
  const last = series.t.length - 1;
  const all = [home, away].flatMap(tm => intervalValues(series, tm, last).map(v => v || 0));
  return Math.max(0.5, Math.ceil(Math.max(...all) * 2) / 2);
}

export function intervalSvg(series, i, home, away, col) {
  const W = 300, H = 132, base = 108, top = intervalTop(series, home, away);
  const blocks = series.intervals, slot = W / blocks.length, bw = Math.min(16, slot / 3);
  const h = intervalValues(series, home, i), a = intervalValues(series, away, i);
  const y = v => base - (v / top) * 96;
  let g = `<line x1="0" x2="${W}" y1="${base}" y2="${base}" stroke="${col.line2}"/>`;
  blocks.forEach((b, k) => {
    const cx = slot * k + slot / 2;
    for (const [vals, c, dx, tm] of [[h, col.home, -bw - 1, home], [a, col.away, 1, away]]) {
      const v = vals[k];
      if (v === null) continue;
      const yy = y(v);
      g += `<rect x="${(cx + dx).toFixed(1)}" y="${yy.toFixed(1)}" width="${bw.toFixed(1)}" height="${(base - yy).toFixed(1)}" rx="2" fill="${c}"><title>${esc(tm)} ${esc(b.label)}: ${n2(v)} npxG</title></rect>`;
    }
    g += `<text x="${cx.toFixed(1)}" y="${H - 6}" text-anchor="middle" class="rl" style="font-size:10px">${esc(b.label)}</text>`;
  });
  return g;
}

export function intervalSummary(series, i, home, away) {
  const h = intervalValues(series, home, i), a = intervalValues(series, away, i);
  const parts = series.intervals.flatMap((b, k) => (h[k] === null ? [] : [`${b.label}: ${home} ${n2(h[k])}, ${away} ${n2(a[k])}`]));
  return `npxG by 15-minute interval up to the replay clock. ${parts.join('; ')}.`;
}

/* ---------- shot outcomes: stacked horizontal bars ---------- */

export const OUTCOMES = [
  ['shots_on_target', 'On target'],
  ['shots_off_target', 'Off target'],
  ['shots_blocked', 'Blocked'],
];

export function outcomeCounts(series, team, i) {
  const t = series.teams[team];
  return OUTCOMES.map(([m]) => t[m][i] || 0);
}

/* Blocked is hatched, so it differs from off target by texture as well as by tone. */
const hatch = c => `repeating-linear-gradient(135deg,${c} 0 2px,transparent 2px 5px)`;

function outcomeColours(team, meta, col) {
  return [team === meta.home ? col.home : col.away, col.muted, hatch(col.muted)];
}

export function outcomeHtml(series, i, meta, col) {
  const teams = [meta.home, meta.away];
  const counts = teams.map(tm => outcomeCounts(series, tm, i));
  const scale = Math.max(1, ...counts.map(c => c[0] + c[1] + c[2]));
  return teams.map((tm, j) => {
    const c = counts[j], total = c[0] + c[1] + c[2], cols = outcomeColours(tm, meta, col);
    const segs = c.map((v, k) => (v ? `<i style="width:${((100 * v) / scale).toFixed(2)}%;background:${cols[k]}"></i>` : '')).join('');
    const words = OUTCOMES.map(([, w], k) => `${c[k]} ${w.toLowerCase()}`).join(', ');
    return `<div class="so"><p class="so-h"><b>${esc(meta.teams[tm].name)}</b> · ${total} shot${total === 1 ? '' : 's'}</p>` +
      `<div class="so-bar" aria-hidden="true">${segs}</div><p class="so-n">${words}</p></div>`;
  }).join('');
}

export function outcomeLegend(meta, col) {
  const cols = [`linear-gradient(90deg,${col.home} 50%,${col.away} 50%)`, col.muted, hatch(col.muted)];
  return OUTCOMES.map(([, w], k) => `<span><i style="background:${cols[k]}"></i>${w}</span>`).join('');
}

/* ---------- result probability: one three-part bar ---------- */

export function resultHtml(series, i, meta, col) {
  const [h, d, a] = series.result_pct[i];
  const segs = [[h, col.home], [d, col.line2], [a, col.away]]
    .map(([v, c]) => (v ? `<i style="width:${v}%;background:${c}"></i>` : '')).join('');
  return `<div class="rp-bar" aria-hidden="true">${segs}</div>` +
    `<p class="rp-n"><span class="c-home">${esc(meta.home)} win <b>${h}%</b></span>` +
    `<span>Draw <b>${d}%</b></span><span class="c-away">${esc(meta.away)} win <b>${a}%</b></span></p>`;
}
