// tests/routing_cases.json against docs/js/routing.js, with the rules the bundle carries.
// tests/test_routing_cases.py runs the same cases against app/router.py.
// Run: node --test tests/js/*.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from '../../docs/js/routing.js';

const read = rel => JSON.parse(readFileSync(new URL(rel, import.meta.url), 'utf8'));
const CASES = read('../routing_cases.json');
const RULES = read('../../docs/data/hero.json').meta.routing;

for (const c of CASES) {
  test(c.id, () => {
    const reasons = c.autopilot_ok ? [RULES.autopilot_reason] : [];
    const got = resolve(RULES, c.tier, c.ttl_s, reasons, c.elapsed_s, c.decision, c.decision_at_s);
    assert.deepEqual(got, c.expect);
  });
}

test('the case table is not empty', () => {
  assert.ok(CASES.length > 20);
});
