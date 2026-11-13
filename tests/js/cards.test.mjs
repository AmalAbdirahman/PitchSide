// P13: the overlay's pure drawing functions (docs/js/cards.js) on the real bundle's cues.
// Run: node --test "tests/js/*.test.mjs"   (Node 22, no npm)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bugHtml, cardHtml, minuteOf, programState, scoreAt, sideOf } from '../../docs/js/cards.js';

const hero = JSON.parse(readFileSync(new URL('../../docs/data/hero.json', import.meta.url), 'utf8'));
const { meta } = hero;
const cues = hero.facts.flatMap(f => f.renditions.filter(r => r.kind === 'cue').map(r => ({ ...r, fact_id: f.fact_id })));
const first = layout => cues.find(c => c.layout === layout && c.lang === 'en');

test('every layout in the bundle draws its numbers and text', () => {
  for (const layout of ['lower_third', 'ticker', 'stat_comparison', 'player_card']) {
    const cue = first(layout);
    assert.ok(cue, `the bundle has a ${layout} cue`);
    const html = cardHtml(cue.card, sideOf(cue.card, meta));
    assert.match(html, new RegExp(`g-${layout === 'lower_third' ? 'lower' : layout === 'stat_comparison' ? 'compare' : layout.replace('_card', '')}`));
    const plain = s => s.replace(/&#39;/g, "'");
    assert.ok(plain(html).includes(cue.card.text.split(' ')[0]));
  }
  const cmp = first('stat_comparison').card;
  const html = cardHtml(cmp, sideOf(cmp, meta));
  assert.ok(html.includes(cmp.home.shown) && html.includes(cmp.away.shown));
  const pc = first('player_card').card;
  assert.ok(cardHtml(pc).includes(pc.figure) && cardHtml(pc).includes(pc.name));
});

test('text is escaped', () => {
  const html = cardHtml({ layout: 'lower_third', label: '<b>', text: 'a & "b"' });
  assert.ok(!html.includes('<b>') && html.includes('&lt;b&gt;') && html.includes('&amp;') && html.includes('&quot;'));
});

test('clock and score follow the bundle', () => {
  const c = meta.clock;
  assert.equal(minuteOf(c, 0), 1);
  assert.equal(minuteOf(c, 2699), 45);
  assert.equal(minuteOf(c, c.period_starts[1]), 46);
  const goal = hero.score_timeline[1];
  assert.deepEqual(scoreAt(hero.score_timeline, goal.t - 1, meta.home, meta.away), { [meta.home]: 0, [meta.away]: 0 });
  assert.equal(scoreAt(hero.score_timeline, goal.t, meta.home, meta.away)[meta.home], goal[meta.home]);
});

test('program state carries the bug and the cue card only', () => {
  const cue = first('player_card');
  const st = programState(meta, hero.score_timeline, 4000, cue);
  assert.deepEqual(Object.keys(st.cue).sort(), ['card', 'fact_id', 'lang', 'layout']);
  assert.equal(st.home.code, meta.home);
  assert.match(bugHtml(st), new RegExp(`${st.home.score}–${st.away.score}`));
  assert.equal(programState(meta, hero.score_timeline, 0, null).cue, null);
  assert.equal(sideOf(cue.card, meta), cue.card.team === meta.home ? 'home' : 'away');
});
