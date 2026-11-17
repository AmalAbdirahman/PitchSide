/* Bundle loading, shared state and small helpers. No network beyond the two local data files.
   D (bundle), CUES and S (replay state) are live bindings: other modules read them, and only
   setBundle and setState replace them. */

export const MATCH_S_PER_S = 40;  // at 1x, one match minute passes every 1.5 real seconds
export const LANES = ['left', 'centre', 'right'];
/* Colours for SVG drawings, read from the CSS tokens in styles.css. readColours() refills COL;
   main.js calls it at start and on every theme change, then redraws. */
const TOKENS = {
  bg: '--bg', card: '--card', line: '--line', line2: '--line-2', text: '--text', muted: '--muted',
  accent: '--accent', verified: '--verified', home: '--home', away: '--away', homeText: '--home-text', awayText: '--away-text',
  pitch: '--pitch', pitchLine: '--pitch-line',
  grp1: '--grp-1', grp2: '--grp-2', grp3: '--grp-3', grp4: '--grp-4', grp5: '--grp-5', grpInk: '--grp-ink',
  airBg: '--air-bg', airText: '--air-text', airAccent: '--air-accent',
};
export const COL = {};
export function readColours() {
  const css = getComputedStyle(document.documentElement);
  for (const [key, token] of Object.entries(TOKENS)) COL[key] = css.getPropertyValue(token).trim();
}

export let D = null;      // bundle: meta (with routing), events, items, players, end
export let CUES = [];     // docs/data/cues.json
export let S = null;      // replay state, see state.js freshState
export const UI = { lang: 'en', depth: 'casual', follow: '', auto: false, view: 'bc', speed: 1 };

export function setBundle(d, cues) {
  D = d;
  CUES = cues;
}

export function setState(s) {
  S = s;
}

/* ---------- helpers ---------- */
export const $ = sel => document.querySelector(sel);
export const $$ = sel => [...document.querySelectorAll(sel)];
export const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pad = n => String(n).padStart(2, '0');
export const clock = t => pad(Math.floor(t / 60)) + ':' + pad(Math.floor(t % 60));
export const minute = t => Math.floor(t / 60) + 1 + "'";
export const nameOf = id => (D.players[id] ? D.players[id].name : id || '');
export const rend = (f, kind, lang) => f.renditions.find(r => r.kind === kind && r.lang === lang) || null;
export const teamCol = team => (team === D.meta.home ? COL.home : COL.away);    // marks
export const teamInk = team => (team === D.meta.home ? COL.homeText : COL.awayText);  // text
export const teamSide = team => (team === D.meta.home ? 'home' : 'away');      // CSS class
export const idxAt = t => Math.min(D.series.t.length - 1, Math.floor(t / D.series.step_s));
export const fix = (v, d) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

/* Event coordinates are per team, attacking left to right. On screen the home side attacks right. */
export function toScreen(team, x, y) {
  const p = D.meta.pitch;
  return team === D.meta.home ? [x, y] : [p.length - x, p.width - y];
}

/* ---------- loading ---------- */
export async function getJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(path + ' returned ' + res.status);
  return res.json();
}

export async function loadBundle() {
  const [hero, cues] = await Promise.all([getJson('data/hero.json'), getJson('data/cues.json')]);
  setBundle(prepare(hero), cues);
}

function prepare(hero) {
  const players = {};
  for (const [team, info] of Object.entries(hero.meta.teams)) {
    for (const [id, name] of Object.entries(info.players)) players[id] = { name, team };
  }
  const items = hero.facts.map(f => toItem(f, false)).concat(hero.log.map(f => toItem(f, true)));
  items.sort((a, b) => a.t - b.t || a.id.localeCompare(b.id));
  const events = hero.events.slice().sort((a, b) => a.t - b.t);
  const last = Math.max(events[events.length - 1].t, items[items.length - 1].t);
  return { meta: hero.meta, metrics: hero.metrics, scores: hero.score_timeline, series: hero.series, events, items, players,
    byId: new Map(hero.events.map(e => [e.id, e])), end: last };
}

function toItem(f, rejected) {
  const ids = new Set(f.evidence.players || []);
  if (f.claim.subject.type === 'player') ids.add(f.claim.subject.id);
  return {
    id: f.fact_id, f, rejected,
    t: rejected ? f.created_t : f.approval.valid_from_t,
    tier: rejected ? null : f.risk.tier,
    reasons: rejected ? [] : f.risk.reasons || [],
    status: f.verification.status,
    hedge: f.uncertainty ? f.uncertainty.hedge : 'none',
    ttl: rejected ? 0 : f.approval.ttl_s || 0,
    players: [...ids],
  };
}
