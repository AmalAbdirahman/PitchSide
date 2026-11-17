/* Draws the R3 match stats (docs/js/stats.js) into the Broadcast bottom row and the Analysis view,
   up to the replay clock. main.js calls renderStats with the KPI row: once per series sample, and on
   every full redraw (theme change, restart). */

import { D, S, $, COL, idxAt, minute } from './data.js';
import { intervalSvg, intervalSummary, outcomeHtml, outcomeLegend, resultHtml } from './stats.js';

const VIEWS = ['bc', 'an'];

export function renderStats() {
  const i = idxAt(S.t), { home, away } = D.meta, s = D.series;
  const upto = `to ${minute(S.t)}`;
  for (const v of VIEWS) {
    const svg = $(`#st-int-${v}`);
    svg.innerHTML = intervalSvg(s, i, home, away, COL);
    svg.setAttribute('aria-label', intervalSummary(s, i, home, away));
    $(`#st-so-${v}`).innerHTML = outcomeHtml(s, i, D.meta, COL);
    $(`#st-rp-${v}`).innerHTML = resultHtml(s, i, D.meta, COL);
    $(`#st-so-t-${v}`).textContent = upto;
    // Legends too: their swatches take the theme's colours.
    $(`#st-key-${v}`).innerHTML = `<span><i style="background:${COL.home}"></i>${home}</span><span><i style="background:${COL.away}"></i>${away}</span>`;
    $(`#st-so-k-${v}`).innerHTML = outcomeLegend(D.meta, COL);
  }
}
