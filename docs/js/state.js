/* Replay state machine. Each routed fact keeps the seconds since it was routed (real seconds,
   paused with the replay) and the first decision taken on it. Its state is not stored by hand:
   routing.js resolves it every frame from the rules in hero.json, and this module reacts to the
   changes (log, counters, air queue). */

import { D, S, UI, MATCH_S_PER_S, rend } from './data.js';
import { resolve } from './routing.js';

export const PHASE = {
  veto: ['Veto window', 'c-verified'], queued: ['Awaiting producer', 'c-corrected'], aired: ['Aired', 'c-verified'],
  vetoed: ['Vetoed', 'c-rejected'], onair: ['On air now', 'c-verified'], declined: ['Rejected by producer', 'c-rejected'], expired: ['Expired · not aired', 'c-rejected'],
  rejected: ['Rejected by verifier', 'c-rejected'],
};
const PHASE_OF = {
  auto_approved: 'aired', approved: 'aired', veto_window: 'veto', queued: 'queued',
  vetoed: 'vetoed', rejected: 'declined', expired: 'expired',
};

export function freshState() {
  return {
    t: 0, playing: false, next: 0, evNext: 0, rt: {}, seen: new Set(), log: [], fx: [],
    airQ: [], onAir: null, lastAired: null, dirty: true, feedDirty: true,
    c: { drafts: 0, verified: 0, corrected: 0, rejected: 0, aired: 0, vetoed: 0, declined: 0, expired: 0 },
  };
}

export function log(text, cls) {
  S.log.unshift({ t: S.t, text, cls: cls || '' });
  S.dirty = true;
}

/* ---------- rules ---------- */
export const tierRule = it => D.meta.routing.tiers[it.tier];
const autopilotOk = it => it.reasons.includes(D.meta.routing.autopilot_reason);

/* The decisions a producer can take on this fact (autopilot is the toggle, not a button). */
export const producerDecisions = it => Object.keys(tierRule(it).decisions).filter(d => d !== 'autopilot');

/* Still waiting: in its tier's starting state, with an expiry that will move it on. */
export function actionable(it) {
  const rt = S.rt[it.id], rule = it.tier && tierRule(it);
  return Boolean(rt && rule && rule.on_timeout && rt.state === rule.initial.state);
}

export const timeLeft = it => it.ttl - S.rt[it.id].elapsed;

export function oldestFor(decision) {
  return D.items.find(it => actionable(it) && Object.hasOwn(tierRule(it).decisions, decision));
}

/* ---------- transitions ---------- */
function take(it, decision) {
  const rt = S.rt[it.id];
  if (rt.decision !== null || !Object.hasOwn(tierRule(it).decisions, decision)) return false;
  if (decision === 'autopilot' && !autopilotOk(it)) return false;
  Object.assign(rt, { decision, decisionAt: rt.elapsed });
  return true;
}

function update(it) {
  const rt = S.rt[it.id];
  const { state, by } = resolve(D.meta.routing, it.tier, it.ttl, it.reasons, rt.elapsed, rt.decision, rt.decisionAt);
  if (state === rt.state) return;
  const from = rt.state;
  Object.assign(rt, { state, by, phase: PHASE_OF[state] });
  announce(it, from, state, by);
}

function announce(it, from, state, by) {
  const id = it.id;
  if (state === 'veto_window') return log(`${id} routed ${it.tier}: ${it.ttl} s veto window`, 'c-verified');
  if (state === 'queued') return log(`${id} routed ${it.tier}: waiting for producer, ${it.ttl} s`, 'c-corrected');
  if (state === 'vetoed') { S.c.vetoed++; return log(`Producer vetoed ${id}; it will not air`, 'c-rejected'); }
  if (state === 'rejected') { S.c.declined++; return log(`Producer rejected ${id}; it will not air`, 'c-rejected'); }
  if (state === 'expired') { S.c.expired++; return log(`${id} expired unanswered and was never aired`, 'c-rejected'); }
  if (from === 'veto_window') log(`Veto window closed for ${id}`);
  else if (by === 'producer') log(`Producer approved ${id}`, 'c-verified');
  else if (by === 'autopilot') log(`Autopilot approved ${id}`, 'c-verified');
  air(it, by);
}

function air(it, by) {
  S.c.aired++;
  S.airQ.push(it);
  log(`Aired ${it.id} (${by})`, 'c-verified');
  // One lower-third slot: if facts pile up, the newest waiting one replaces older waiting ones on screen.
  while (S.airQ.length > 1) {
    const old = S.airQ.shift();
    S.rt[old.id].superseded = true;
    log(`Lower third for ${old.id} skipped on screen: newer fact ${it.id} was waiting`);
  }
}

function admit(it) {
  S.c.drafts++;
  S.rt[it.id] = { phase: null, state: null, by: null, elapsed: 0, decision: null, decisionAt: null };
  if (it.rejected) {
    S.rt[it.id].phase = 'rejected';
    S.c.rejected++;
    log(`Verifier rejected ${it.id}: ${it.f.verification.reject_reason}`, 'c-rejected');
    return;
  }
  if (it.status === 'corrected') {
    S.c.corrected++;
    log(`Verifier corrected ${it.id}: ${it.f.verification.corrections.map(c => c.reason).join('; ')}`, 'c-corrected');
  } else {
    S.c.verified++;
    log(`Verifier passed ${it.id}`, 'c-verified');
  }
  if (UI.auto) take(it, 'autopilot');
  update(it);
}

/* ---------- inputs ---------- */
export function decide(id, decision) {
  const it = D.items.find(x => x.id === id);
  if (it && actionable(it) && take(it, decision)) update(it);
}

export function setAutopilot(on) {
  UI.auto = on;
  log(`Autopilot ${on ? 'on' : 'off'}`);
  if (!on) return;
  for (const it of D.items) if (actionable(it) && take(it, 'autopilot')) update(it);
}

/* ---------- time ---------- */
export function tickTimers(dt) {
  for (const it of D.items) {
    if (!actionable(it)) continue;
    S.rt[it.id].elapsed += dt;
    update(it);
  }
}

export function tickOnAir(dt) {
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
  if (it.f.kind === 'score_event') S.fx.push('flash');
  S.dirty = true;
}

export function advance(dt) {
  S.t = Math.min(D.end, S.t + dt * UI.speed * MATCH_S_PER_S);
  while (S.next < D.items.length && D.items[S.next].t <= S.t) admit(D.items[S.next++]);
  while (S.evNext < D.events.length && D.events[S.evNext].t <= S.t) {
    if (D.events[S.evNext].type === 'goal') S.fx.push('pop');
    S.evNext++;
    S.feedDirty = true;
  }
  if (S.t >= D.end) {
    S.playing = false;
    S.fx.push('ended');
  }
}
