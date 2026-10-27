/* Sidebar and environment destinations start at the top; ordinary returns retain position. */
(() => {
  const key = 'workspace-sidebar-navigation';
  window.WorkspaceNavigation = {
    start(destination) {
      const url = destination ? new URL(destination, location.href) : null;
      const stamp = url ? JSON.stringify({at:Date.now(),path:url.pathname+url.search}) : String(Date.now());
      try { sessionStorage.setItem(key, stamp); } catch (_) {}
      return stamp;
    },
    cancel(stamp) {
      try { if (sessionStorage.getItem(key) === stamp) sessionStorage.removeItem(key); } catch (_) {}
    }
  };
  document.addEventListener('click', event => {
    const link = event.target.closest('#primary-sidebar a[href]');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey ||
        link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
    const destination = new URL(link.href, location.href);
    if (destination.origin !== location.origin) return;
    window.WorkspaceNavigation.start();
  });
  // Registered before both list-position handlers, including on BFCache returns.
  window.addEventListener('pageshow', () => {
    window.SidebarNavigation = false;
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
      const stamp = typeof saved === 'number' ? saved : saved?.at;
      sessionStorage.removeItem(key);
      const matches = typeof saved === 'number' || saved?.path === location.pathname + location.search;
      window.SidebarNavigation = matches && stamp > 0 && Date.now() - stamp >= 0 && Date.now() - stamp < 30000;
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
