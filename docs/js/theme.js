/* Theme choice: "system" (follow prefers-color-scheme), "light" or "dark". A classic script
   loaded in <head>, so the saved choice is applied before the first paint. The header button
   cycles the choice; it is remembered in localStorage when storage is available. Every change
   of the effective theme fires a "themechange" event on document, so drawings can redraw. */
(function () {
  const KEY = 'pitchside-theme';
  const ORDER = ['system', 'light', 'dark'];
  const NAME = { system: 'System', light: 'Light', dark: 'Dark' };
  const root = document.documentElement;
  const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;

  function load() {
    try {
      const v = localStorage.getItem(KEY);
      return ORDER.includes(v) ? v : 'system';
    } catch (err) {  // storage blocked (private mode, file://): fall back to the system setting
      return 'system';
    }
  }

  function save(choice) {
    try {
      localStorage.setItem(KEY, choice);
    } catch (err) {  // storage blocked: the choice lasts for this page only
    }
  }

  let choice = load();

  function apply() {
    if (choice === 'system') delete root.dataset.theme;
    else root.dataset.theme = choice;
    const btn = document.getElementById('theme');
    if (!btn) return;
    document.getElementById('theme-v').textContent = NAME[choice];
    const next = ORDER[(ORDER.indexOf(choice) + 1) % ORDER.length];
    btn.setAttribute('aria-label', `Theme: ${NAME[choice]}. Switch to ${NAME[next]}.`);
  }

  function changed() {
    document.dispatchEvent(new CustomEvent('themechange'));
  }

  apply();
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.getElementById('theme').addEventListener('click', () => {
      choice = ORDER[(ORDER.indexOf(choice) + 1) % ORDER.length];
      save(choice);
      apply();
      changed();
    });
  });
  if (media) media.addEventListener('change', () => { if (choice === 'system') changed(); });
})();
