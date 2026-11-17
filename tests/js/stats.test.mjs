// R3: the match-stat drawings (docs/js/stats.js) on the real bundle's series.
// Run: node --test "tests/js/*.test.mjs"   (Node 22, no npm)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { intervalSvg, intervalSummary, intervalValues, outcomeCounts, outcomeHtml, resultHtml } from '../../docs/js/stats.js';

const hero = JSON.parse(readFileSync(new URL('../../docs/data/hero.json', import.meta.url), 'utf8'));
const { meta, series } = hero;
const { home, away } = meta;
const last = series.t.length - 1;
const COL = { home: 'H', away: 'A', muted: 'M', line: 'L', line2: 'L2', text: 'T', verified: 'STATUS', corrected: 'STATUS', rejected: 'STATUS' };
const at = minute => series.t.findIndex(t => t >= minute * 60);

test('interval bars appear only for intervals that have started', () => {
  assert.deepEqual(intervalValues(series, home, 0).filter(v => v !== null).length, 1);
  const mid = at(50);  // second half under way: four intervals started
  assert.equal(intervalValues(series, home, mid).filter(v => v !== null).length, 4);
  const bars = svg => (svg.match(/<rect /g) || []).length;
  assert.equal(bars(intervalSvg(series, mid, home, away, COL)), 8);
  assert.equal(bars(intervalSvg(series, last, home, away, COL)), 12);
});

test('interval bars state the bundle numbers and labels', () => {
  const svg = intervalSvg(series, last, home, away, COL);
  for (const b of series.intervals) assert.ok(svg.includes(`>${b.label}</text>`), b.label);
  const v = intervalValues(series, home, last)[5];
  assert.ok(svg.includes(`${home} 76–90+: ${v.toFixed(2)} npxG`));
  assert.match(intervalSummary(series, last, home, away), /^npxG by 15-minute interval/);
});

test('shot outcome bars draw the bundle counts, never more than the shots', () => {
  for (const tm of [home, away]) {
    const c = outcomeCounts(series, tm, last);
    assert.equal(c[0] + c[1] + c[2], series.teams[tm].shots[last]);
  }
  const html = outcomeHtml(series, last, meta, COL);
  const [on, off, blocked] = outcomeCounts(series, home, last);
  assert.ok(html.includes(`${on} on target, ${off} off target, ${blocked} blocked`));
  assert.ok(html.includes(meta.teams[home].name));
  assert.ok(!html.includes('STATUS'), 'verifier status colours are not used for outcomes');
});

test('result probability shows whole percentages that add to 100', () => {
  for (const i of [0, at(30), last]) {
    const [h, d, a] = series.result_pct[i];
    assert.equal(h + d + a, 100);
    const html = resultHtml(series, i, meta, COL);
    assert.ok(html.includes(`${home} win <b>${h}%</b>`) && html.includes(`Draw <b>${d}%</b>`) && html.includes(`${away} win <b>${a}%</b>`));
  }
});
