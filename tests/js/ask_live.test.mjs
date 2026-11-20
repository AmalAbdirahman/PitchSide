// R6: the Live (local) tab's states and question limits (docs/js/ask_live.js), no network.
// Run: node --test "tests/js/*.test.mjs"   (Node 22, no npm)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { cleanQuestion, liveView, LIVE_URL } from '../../docs/js/ask_live.js';

test('the text box is enabled only while the local server is up', () => {
  for (const state of ['built', 'checking', 'asking', 'down']) assert.equal(liveView(state).input, false, state);
  const up = liveView('up', { max_chars: 200 });
  assert.equal(up.input, true);
  assert.equal(up.status, 'Live · running on this machine');
  assert.match(up.hint, /200 characters/);
});

test('when no server answers, the panel says how to start one', () => {
  assert.match(liveView('down').hint, /python scripts\/ask_server\.py/);
  assert.match(liveView('built').hint, /Live questions run locally/);
});

test('questions are trimmed and limited', () => {
  assert.equal(cleanQuestion('  Who shot most?  ', 200), 'Who shot most?');
  assert.equal(cleanQuestion('   ', 200), null);
  assert.equal(cleanQuestion('x'.repeat(201), 200), null);
  assert.equal(cleanQuestion('x'.repeat(200), 200), 'x'.repeat(200));
});

test('the server address is loopback on the configured port', () => {
  const cfg = readFileSync(new URL('../../config.yaml', import.meta.url), 'utf8');
  const port = cfg.match(/^ {4}port: (\d+)/m)[1]  // ask.live.port, the only key at that depth;
  assert.equal(LIVE_URL, `http://127.0.0.1:${port}`);
});
