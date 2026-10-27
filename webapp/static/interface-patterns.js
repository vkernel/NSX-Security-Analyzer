/* Progressive enhancements only. Server authorization and form behavior remain authoritative. */
(() => {
  const main = document.getElementById('main');
  if (!main) return;
  function enhance() {
    for (const region of main.querySelectorAll('.table-scroll')) {
      const overflow = region.scrollWidth > region.clientWidth;
      if (overflow) {
        region.tabIndex = 0;
        region.setAttribute('role', 'region');
        if (!region.hasAttribute('aria-label')) region.setAttribute('aria-label', 'Scrollable table — use arrow keys to view more columns');
      } else if (region.getAttribute('aria-label') === 'Scrollable table — use arrow keys to view more columns') {
        region.removeAttribute('tabindex'); region.removeAttribute('role'); region.removeAttribute('aria-label');
      }
    }
    for (const menu of main.querySelectorAll('.cleanup-task-actions details,.selection-action-menu,details.task-actions')) menu.dataset.actionMenu = 'true';
  }
  enhance();
  let pending = false;
  const schedule = () => { if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; enhance(); }); } };
  new MutationObserver(schedule).observe(main, {childList:true, subtree:true});
  window.addEventListener('resize', schedule);
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const menu = event.target.closest('[data-action-menu][open]');
    if (menu) { menu.open = false; menu.querySelector('summary')?.focus(); event.preventDefault(); }
  });
  document.addEventListener('click', event => {
    for (const menu of main.querySelectorAll('[data-action-menu][open]')) if (!menu.contains(event.target)) menu.open = false;
  });
  // Store position only for navigation from a result list; never persist field values.
  const storageKey = 'ui-list-position:' + location.pathname + location.search;
  main.addEventListener('click', event => {
    if (!event.target.closest('table a[href],.environment-grid a[href]')) return;
    try { sessionStorage.setItem(storageKey, JSON.stringify({y:scrollY,at:Date.now()})); } catch (_) {}
  });
  window.addEventListener('pageshow', () => {
    if (window.SidebarNavigation) return;
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey));
      if (saved && Date.now() - saved.at < 3600000) requestAnimationFrame(() => scrollTo(0,saved.y));
      sessionStorage.removeItem(storageKey);
    } catch (_) {}
  });
  const selection = document.querySelector('[data-preview-selection]');
  if (selection) {
    const button = selection.querySelector('[data-preview-selected]');
    const count = selection.querySelector('[data-selected-count]');
    const update = () => {
      const n = selection.querySelectorAll('input[name="finding"]:checked').length;
      count.textContent = `${n} selected`;
      button.disabled = n === 0 || n > 100;
      button.textContent = n ? `Preview ${n} selected object${n===1?'':'s'}` : 'Select objects to preview';
    };
    selection.addEventListener('change', update); update();
  }
})();
