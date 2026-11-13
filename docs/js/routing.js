/* Routing over time, interpreted from the rules table in hero.json (meta.routing).
   Pure: no DOM, no state. app/router.py resolve_state is the Python twin, and
   tests/routing_cases.json runs against both (pytest and node --test). */

const outcome = o => ({ state: o.state, by: o.by ?? null });

/**
 * State and who decided, `elapsed` seconds after a fact was routed.
 * `decision` is the first decision taken (by the producer, or by autopilot when it was switched on,
 * at 0 if it was already on). It counts only if the tier allows it and it came before the expiry.
 */
export function resolve(rules, tier, ttl, reasons, elapsed, decision = null, decisionAt = null) {
  const rule = rules.tiers[tier];
  if (!rule.on_timeout) return outcome(rule.initial);
  const taken = decision !== null && Object.hasOwn(rule.decisions, decision) ? rule.decisions[decision] : null;
  const inTime = decisionAt !== null && decisionAt < ttl && decisionAt <= elapsed;
  const allowed = decision !== 'autopilot' || reasons.includes(rules.autopilot_reason);
  if (taken && inTime && allowed) return outcome(taken);
  if (elapsed >= ttl) return outcome(rule.on_timeout);
  return outcome(rule.initial);
}
