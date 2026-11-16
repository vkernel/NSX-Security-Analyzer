/* Grouped sidebar: collapsible rail with flyouts, page disclosure and a search palette.
   Server-rendered links stay authoritative; this script only changes presentation. */
(() => {
  'use strict';
  const desktop = () => matchMedia('(min-width:801px)').matches;
  const csrf = () => document.querySelector('#interface-csrf input')?.value || '';

  function initCollapse(shell) {
    const toggle = shell.querySelector('[data-section-toggle]');
    if (!toggle) return;
    const status = shell.querySelector('[data-section-nav-status]');
    const key = 'menu:workspace:section-panel';
    let expanded = document.body.dataset.sectionExpanded !== 'false', saving = false, queued;
    function paint() {
      document.body.dataset.sectionExpanded = String(expanded);
      const label = expanded ? 'Collapse navigation' : 'Expand navigation';
      toggle.setAttribute('aria-expanded', String(expanded));
      toggle.setAttribute('aria-label', label); toggle.title = label;
      const text = toggle.querySelector('span'); if (text) text.textContent = expanded ? 'Collapse' : 'Expand';
      closeFlyouts(shell);
    }
    async function save() {
      if (document.body.dataset.rememberMenus !== 'true') return;
      queued = expanded; if (saving) return; saving = true;
      try {
        while (typeof queued === 'boolean') {
          const value = queued; queued = undefined;
          try {
            const response = await fetch(toggle.dataset.saveUrl, {method: 'POST', keepalive: true,
              headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()},
              body: JSON.stringify({key, value}), signal: AbortSignal.timeout(10000)});
            if (!response.ok || response.redirected) throw Error();
            if (status) status.textContent = '';
          } catch (_) { if (status) status.textContent = 'Menu preference could not be saved. Try again.'; }
        }
      } finally { saving = false; }
    }
    paint();
    toggle.addEventListener('click', () => { expanded = !expanded; paint(); save(); });
    shell.navToggle = () => toggle.click();
    shell.navExpanded = () => expanded;
  }

  const collapsed = () => desktop() && document.body.dataset.sectionExpanded === 'false';

  function setOpen(module, open) {
    const pages = module.querySelector('[data-nav-pages]'), button = module.querySelector('[data-nav-disclosure]');
    if (!pages || !button) return;
    pages.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
    const label = module.querySelector('[data-module-label]')?.dataset.moduleLabel || '';
    button.setAttribute('aria-label', (open ? 'Hide ' : 'Show ') + label + ' pages');
  }

  function closeFlyouts(shell, except) {
    shell.querySelectorAll('.nav-module.is-flyout').forEach(module => {
      if (module === except) return;
      module.classList.remove('is-flyout');
      const pages = module.querySelector('[data-nav-pages]');
      pages.style.top = '';
      pages.hidden = module.dataset.wasOpen !== 'true';
      module.querySelector('[data-nav-flyout-trigger]')?.setAttribute('aria-expanded', 'false');
    });
  }

  function openFlyout(shell, module) {
    if (!collapsed() || module.classList.contains('is-flyout')) return;
    closeFlyouts(shell, module);
    const pages = module.querySelector('[data-nav-pages]');
    if (!pages) return;
    module.dataset.wasOpen = String(!pages.hidden);
    module.classList.add('is-flyout');
    pages.hidden = false;
    const row = module.querySelector('.nav-row').getBoundingClientRect();
    const height = pages.offsetHeight;
    pages.style.top = Math.max(8, Math.min(row.top - 6, innerHeight - height - 8)) + 'px';
    module.querySelector('[data-nav-flyout-trigger]')?.setAttribute('aria-expanded', 'true');
  }

  function initTree(shell) {
    const modules = [...shell.querySelectorAll('.nav-module.has-pages')];
    let leaveTimer, returningFocus = false;
    modules.forEach(module => {
      const trigger = module.querySelector('[data-nav-flyout-trigger]');
      module.querySelector('[data-nav-disclosure]')?.addEventListener('click', () => {
        const pages = module.querySelector('[data-nav-pages]');
        setOpen(module, pages.hidden);
      });
      module.addEventListener('mouseenter', () => { clearTimeout(leaveTimer); openFlyout(shell, module); });
      module.addEventListener('mouseleave', () => { leaveTimer = setTimeout(() => closeFlyouts(shell), 180); });
      module.addEventListener('focusin', () => { if (!returningFocus) openFlyout(shell, module); });
      trigger?.addEventListener('click', event => {
        if (!collapsed()) return;
        // In the rail, the module icon opens its pages; the pages hold the links.
        event.preventDefault();
        if (!module.classList.contains('is-flyout')) openFlyout(shell, module);
        module.querySelector('[data-nav-pages] a')?.focus();
      });
    });
    shell.addEventListener('focusout', event => {
      if (!event.relatedTarget || !event.relatedTarget.closest('.nav-module.is-flyout')) {
        const open = shell.querySelector('.nav-module.is-flyout');
        if (open && !open.contains(event.relatedTarget)) closeFlyouts(shell);
      }
    });
    shell.addEventListener('keydown', event => {
      const open = shell.querySelector('.nav-module.is-flyout');
      if (event.key === 'Escape' && open) {
        closeFlyouts(shell);
        returningFocus = true; open.querySelector('[data-nav-flyout-trigger]')?.focus(); returningFocus = false;
      }
    });
    addEventListener('resize', () => closeFlyouts(shell));
  }

  /* Search palette: local pages and environments, plus saved objects from the server. */
  function initPalette(shell) {
    const dialog = document.getElementById('command-palette');
    if (!dialog || typeof dialog.showModal !== 'function') return;
    const input = dialog.querySelector('[data-command-input]'), list = dialog.querySelector('[data-command-results]');
    const status = dialog.querySelector('[data-command-status]');
    let index = [];
    try { index = JSON.parse(document.getElementById('navigation-index')?.textContent || '[]'); } catch (_) { index = []; }
    const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
    document.querySelectorAll('[data-command-shortcut]').forEach(k => { k.textContent = mac ? '⌘K' : 'Ctrl K'; });
    let items = [], active = 0, remote = [], controller, timer, opener;

    function actions() {
      const expanded = shell.navExpanded ? shell.navExpanded() : true;
      return [
        {type: 'Actions', label: expanded ? 'Collapse navigation' : 'Expand navigation', meta: 'Sidebar', run: () => shell.navToggle?.()},
        {type: 'Actions', label: 'Switch colour theme', meta: 'Light or dark', run: () => document.querySelector('.relay-theme-toggle')?.click()},
      ];
    }
    function matches(entry, words) {
      const text = (entry.label + ' ' + (entry.meta || '') + ' ' + (entry.kind || '')).toLowerCase();
      return words.every(word => text.includes(word));
    }
    function compute() {
      const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
      const local = index.concat(actions()).filter(entry => !words.length || matches(entry, words));
      const pages = local.filter(e => e.type === 'Pages').slice(0, words.length ? 8 : 6);
      const envs = local.filter(e => e.type === 'Environments').slice(0, 5);
      const acts = local.filter(e => e.type === 'Actions');
      items = [...remote, ...pages, ...envs, ...acts];
      active = Math.min(active, Math.max(items.length - 1, 0));
      render();
    }
    function render() {
      list.replaceChildren();
      let group = '';
      items.forEach((item, i) => {
        if (item.type !== group) {
          group = item.type;
          const heading = document.createElement('li');
          heading.className = 'command-group'; heading.setAttribute('role', 'presentation'); heading.textContent = group;
          list.append(heading);
        }
        const option = document.createElement('li');
        option.id = 'command-option-' + i; option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', String(i === active));
        option.className = 'command-option';
        const label = document.createElement('span'); label.className = 'command-label'; label.textContent = item.label;
        const meta = document.createElement('span'); meta.className = 'command-meta'; meta.textContent = item.meta || '';
        option.append(label, meta);
        option.addEventListener('mousemove', () => { if (active !== i) { active = i; paintActive(); } });
        option.addEventListener('click', () => run(item));
        list.append(option);
      });
      input.setAttribute('aria-activedescendant', items.length ? 'command-option-' + active : '');
      if (!items.length) status.textContent = input.value.trim() ? 'No matches in pages, environments or saved objects.' : '';
    }
    function paintActive() {
      list.querySelectorAll('[role=option]').forEach((option, i) => option.setAttribute('aria-selected', String(i === active)));
      input.setAttribute('aria-activedescendant', items.length ? 'command-option-' + active : '');
      document.getElementById('command-option-' + active)?.scrollIntoView({block: 'nearest'});
    }
    function run(item) {
      dialog.close();
      if (item.run) { item.run(); return; }
      if (item.url) location.assign(item.url);
    }
    async function searchObjects() {
      const query = input.value.trim();
      controller?.abort();
      if (query.length < 2) { remote = []; status.textContent = ''; compute(); return; }
      controller = new AbortController();
      status.textContent = 'Searching saved snapshots…';
      try {
        const url = new URL(dialog.dataset.searchUrl, location.href); url.searchParams.set('q', query);
        const response = await fetch(url, {credentials: 'same-origin', headers: {Accept: 'application/json'}, signal: controller.signal});
        if (!response.ok || response.redirected) throw Error();
        const data = await response.json();
        if (input.value.trim() !== query) return;
        remote = (data.results || []).map(entry => Object.assign({type: 'Objects'}, entry));
        status.textContent = data.limited ? 'Showing the first 20 saved objects. Refine the search for more.' : '';
      } catch (error) {
        if (error.name === 'AbortError') return;
        remote = []; status.textContent = 'Saved objects could not be searched. Pages are still listed.';
      }
      compute();
    }
    function open() {
      if (dialog.open) return;
      opener = document.activeElement;
      closeFlyouts(shell);
      input.value = ''; remote = []; active = 0; status.textContent = '';
      compute();
      dialog.showModal(); input.focus();
    }
    document.querySelectorAll('[data-command-open]').forEach(button => button.addEventListener('click', open));
    document.addEventListener('keydown', event => {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 'k') {
        if (document.querySelector('dialog[open]:not(#command-palette)')) return;
        event.preventDefault(); dialog.open ? dialog.close() : open();
      }
    });
    input.addEventListener('input', () => { active = 0; compute(); clearTimeout(timer); timer = setTimeout(searchObjects, 200); });
    input.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown') { event.preventDefault(); if (items.length) { active = (active + 1) % items.length; paintActive(); } }
      else if (event.key === 'ArrowUp') { event.preventDefault(); if (items.length) { active = (active - 1 + items.length) % items.length; paintActive(); } }
      else if (event.key === 'Enter') { event.preventDefault(); if (items[active]) run(items[active]); }
    });
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => { controller?.abort(); clearTimeout(timer); if (opener && opener.isConnected) opener.focus({preventScroll: true}); });
  }

  /* A search result opens the right report table, filtered to the chosen name. */
  function applyFind() {
    const params = new URLSearchParams(location.search), name = params.get('find');
    if (!name || !location.hash) return;
    const slug = location.hash.slice(1);
    const fill = () => {
      const field = document.querySelector('[data-server-table="' + CSS.escape(slug) + '"] .table-tools input');
      if (!field) return false;
      field.value = name; field.dispatchEvent(new Event('input', {bubbles: true}));
      return true;
    };
    if (fill()) return;
    const observer = new MutationObserver(() => { if (fill()) observer.disconnect(); });
    observer.observe(document.getElementById('main') || document.body, {childList: true, subtree: true});
    setTimeout(() => observer.disconnect(), 15000);
  }

  /* Snapshot reports switch sections by hash; keep the sidebar and breadcrumb in step. */
  // Report sub-views (the view chips) belong to their list page in the sidebar.
  const PARENT_VIEWS = {
    'unused-groups': 'all-groups', 'empty-groups': 'all-groups', 'unknown-membership': 'all-groups',
    'unused-services': 'all-services', 'empty-policies': 'dfw-policies',
    'tags-both': 'tags-all', 'tags-vm_only': 'tags-all', 'tags-group_only': 'tags-all', 'tags-other_only': 'tags-all', 'tags-unknown': 'tags-all',
    'zero-hit-rules': 'dfw-rules', 'disabled-rules': 'dfw-rules', 'unknown-statistics': 'dfw-rules', 'empty-group-rules': 'dfw-rules', 'dfw-scope-rules': 'dfw-rules',
  };
  function syncHash(shell) {
    const hash = location.hash;
    if (!hash || hash.length < 2) return;
    const here = location.pathname;
    const matching = target => [...shell.querySelectorAll('[data-nav-pages] a[href]')].filter(link => {
      const url = new URL(link.href, location.href);
      return url.hash === target && url.pathname === here;
    });
    let links = matching(hash);
    const parent = PARENT_VIEWS[decodeURIComponent(hash.slice(1))];
    if (!links.length && parent) links = matching('#' + parent);
    if (!links.length) return;
    const link = links[0], module = link.closest('.nav-module');
    shell.querySelectorAll('[data-nav-pages] a[aria-current]').forEach(a => a.removeAttribute('aria-current'));
    link.setAttribute('aria-current', 'page');
    shell.querySelectorAll('.nav-module.is-active').forEach(m => {
      if (m === module) return;
      m.classList.remove('is-active');
      m.querySelector('[data-primary]')?.removeAttribute('aria-current');
    });
    module.classList.add('is-active');
    module.querySelector('[data-primary]')?.setAttribute('aria-current', 'true');
    if (!collapsed()) setOpen(module, true);
    const crumb = document.querySelector('.topbar-breadcrumb');
    if (crumb) {
      const parts = crumb.querySelectorAll(':scope>span:not([aria-hidden])');
      const label = module.querySelector('[data-module-label]')?.dataset.moduleLabel;
      if (parts[1] && label) parts[1].textContent = label;
      if (parts[2]) parts[2].textContent = link.textContent.trim();
    }
  }

  function init() {
    const shell = document.querySelector('[data-nav-shell]');
    if (shell) { initCollapse(shell); initTree(shell); initPalette(shell); syncHash(shell); addEventListener('hashchange', () => syncHash(shell)); }
    applyFind();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
