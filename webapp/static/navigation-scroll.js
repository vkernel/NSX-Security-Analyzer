/* Sidebar destinations start at the top; returning to a list keeps its position. */
(() => {
  const key = 'workspace-sidebar-navigation';
  document.addEventListener('click', event => {
    const link = event.target.closest('#primary-sidebar a[href]');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey ||
        link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
    const destination = new URL(link.href, location.href);
    if (destination.origin !== location.origin) return;
    try { sessionStorage.setItem(key, String(Date.now())); } catch (_) {}
  });
  // Registered before both list-position handlers, including on BFCache returns.
  window.addEventListener('pageshow', () => {
    window.SidebarNavigation = false;
    try {
      const stamp = Number(sessionStorage.getItem(key));
      sessionStorage.removeItem(key);
      window.SidebarNavigation = stamp > 0 && Date.now() - stamp >= 0 && Date.now() - stamp < 30000;
    } catch (_) {}
    if (!window.SidebarNavigation) return;
    const suffix = location.pathname + location.search;
    try {
      sessionStorage.removeItem('review-position:' + suffix);
      sessionStorage.removeItem('ui-list-position:' + suffix);
    } catch (_) {}
    requestAnimationFrame(() => {
      window.scrollTo({top:0, left:0, behavior:'instant'});
      const main = document.getElementById('main');
      if (main) { main.scrollTop = 0; main.scrollLeft = 0; }
    });
  });
})();
