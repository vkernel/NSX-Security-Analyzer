/* Restore list position only after the user opened a finding from that list. */
(() => {
  const key = 'review-position:' + location.pathname + location.search;
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-review-link]')) return;
    try { sessionStorage.setItem(key, JSON.stringify({y:scrollY, at:Date.now()})); } catch (_) {}
  });
  window.addEventListener('pageshow', () => {
    if (window.SidebarNavigation) return;
    try {
      const saved = JSON.parse(sessionStorage.getItem(key));
      if (saved && Date.now() - saved.at < 3600000) requestAnimationFrame(() => scrollTo(0, saved.y));
      sessionStorage.removeItem(key);
    } catch (_) {}
  });
})();
