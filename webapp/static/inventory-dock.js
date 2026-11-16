/* Groups and VMs: on wide screens the shared evidence drawer docks beside the list
   instead of covering it. The panel has three widths (docked, wide, full screen), a
   resize handle and an "Open in new tab" link. Evidence content, requests and
   authorization are unchanged; narrow screens keep the modal drawer. */
(() => {
  const DOCK_PANELS = ['all-groups', 'empty-groups', 'unused-groups', 'unknown-membership', 'all-vms'];
  const MODES = [['docked', 'Docked', 'Show the panel beside the list'], ['wide', 'Wide', 'Widen the panel; the list stays visible'], ['full', 'Full screen', 'Use the whole window for this evidence']];
  const MIN_WIDTH = 360, KEY_MODE = 'evidence-panel-mode', KEY_WIDTH = 'evidence-panel-width';
  const wide = () => !window.matchMedia || window.matchMedia('(min-width: 1280px)').matches;
  const panel = () => decodeURIComponent((location.hash || '').slice(1));
  const dockable = () => wide() && DOCK_PANELS.includes(panel());
  const read = key => { try { return localStorage.getItem(key); } catch (_) { return null; } };
  const write = (key, value) => { try { localStorage.setItem(key, value); } catch (_) {} };
  const make = (tag, text, className) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (className) node.className = className; return node; };

  function selectRow(row) {
    document.querySelectorAll('.report-content tr[aria-current="true"]').forEach(node => node.removeAttribute('aria-current'));
    if (row) row.setAttribute('aria-current', 'true');
  }

  function init() {
    const dialog = document.getElementById('detail-dialog');
    if (!dialog || !document.body.classList.contains('snapshot-page')) return;
    const heading = dialog.querySelector('.dialog-heading'), body = document.getElementById('detail-body');
    let mode = 'docked', previous = 'docked', chosen = false, objectLink = null;

    // Header controls exist only while the panel is docked.
    const controls = make('div', null, 'evidence-modes'); controls.setAttribute('role', 'group'); controls.setAttribute('aria-label', 'Panel width');
    for (const [value, label, hint] of MODES) {
      const button = make('button', label); button.type = 'button'; button.dataset.mode = value; button.title = hint;
      button.onclick = () => { chosen = true; setMode(value, {remember: true}); };
      controls.append(button);
    }
    const newTab = make('a', 'Open in new tab', 'evidence-new-tab'); newTab.target = '_blank'; newTab.rel = 'noopener'; newTab.hidden = true;
    const handle = make('div', null, 'evidence-resize'); handle.tabIndex = 0;
    handle.setAttribute('role', 'separator'); handle.setAttribute('aria-orientation', 'vertical'); handle.setAttribute('aria-label', 'Resize panel');

    const maxWidth = () => Math.round(window.innerWidth * 0.7);
    function setWidth(px, remember) {
      const value = Math.max(MIN_WIDTH, Math.min(maxWidth(), Math.round(px)));
      document.body.style.setProperty('--dock-width', value + 'px');
      handle.setAttribute('aria-valuemin', String(MIN_WIDTH)); handle.setAttribute('aria-valuemax', String(maxWidth())); handle.setAttribute('aria-valuenow', String(value));
      if (remember) write(KEY_WIDTH, String(value));
    }
    function setMode(value, {remember = false} = {}) {
      if (value === 'full' && mode !== 'full') previous = mode;
      mode = value;
      for (const name of ['docked', 'wide', 'full']) dialog.classList.toggle('mode-' + name, name === value);
      document.body.classList.toggle('evidence-docked-wide', value === 'wide');
      document.body.classList.toggle('evidence-docked-full', value === 'full');
      controls.querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === value)));
      handle.hidden = value !== 'docked';
      if (remember) write(KEY_MODE, value);
    }
    function mount() {
      const close = heading.querySelector('#close-details');
      heading.insertBefore(controls, close); heading.insertBefore(newTab, controls);
      dialog.append(handle);
      setWidth(Number(read(KEY_WIDTH)) || 460, false);
      chosen = false;
      const stored = read(KEY_MODE);
      setMode(['docked', 'wide', 'full'].includes(stored) ? stored : 'docked');
      if (objectLink) { newTab.href = objectLink; newTab.hidden = false; } else newTab.hidden = true;
    }
    function unmount() {
      controls.remove(); newTab.remove(); handle.remove();
      for (const name of ['is-docked', 'mode-docked', 'mode-wide', 'mode-full']) dialog.classList.remove(name);
      document.body.classList.remove('evidence-docked', 'evidence-docked-wide', 'evidence-docked-full');
    }

    const modal = HTMLDialogElement.prototype.showModal;
    dialog.showModal = function () {
      if (dockable()) {
        if (this.open && this.matches(':modal')) return;
        const opening = !this.classList.contains('is-docked');
        document.body.classList.add('evidence-docked');
        this.classList.add('is-docked');
        if (opening) mount();
        this.show();
        return;
      }
      unmount();
      return modal.call(this);
    };
    dialog.addEventListener('close', () => { unmount(); selectRow(null); objectLink = null; });

    // Firewall tables widen the panel automatically unless the user picked a width for this object.
    new MutationObserver(() => {
      if (!dialog.classList.contains('is-docked') || chosen) return;
      const firewall = dialog.classList.contains('evidence-firewall-wide');
      if (firewall && mode === 'docked') setMode('wide');
      else if (!firewall && mode === 'wide' && read(KEY_MODE) !== 'wide') setMode('docked');
    }).observe(dialog, {attributes: true, attributeFilter: ['class']});

    // Long coverage notes collapse into "About this evidence" in the docked panel; the text is unchanged.
    new MutationObserver(() => {
      if (!dialog.classList.contains('is-docked')) return;
      body.querySelectorAll('section.evidence-table-section:not([data-about])').forEach(section => {
        const notes = [...section.children].filter(node => node.matches('p.evidence-note'));
        if (!notes.length) return;
        section.dataset.about = '';
        const about = make('details', null, 'evidence-about'), summary = make('summary', 'About this evidence');
        about.append(summary, ...notes);
        (section.querySelector(':scope > .evidence-table-status') || section.firstElementChild).after(about);
      });
    }).observe(body, {childList: true, subtree: true});

    // Resize by dragging the panel's left edge or with the arrow keys.
    handle.addEventListener('pointerdown', event => {
      event.preventDefault(); handle.setPointerCapture?.(event.pointerId);
      const move = e => setWidth(window.innerWidth - e.clientX - 16, false);
      const stop = e => { handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', stop); setWidth(window.innerWidth - e.clientX - 16, true); };
      handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', stop);
    });
    handle.addEventListener('keydown', event => {
      const current = Number(handle.getAttribute('aria-valuenow')) || 460;
      if (event.key === 'ArrowLeft') { event.preventDefault(); setWidth(current + 32, true); }
      if (event.key === 'ArrowRight') { event.preventDefault(); setWidth(current - 32, true); }
    });

    // Remember which row opened the panel; window capture runs before the evidence handlers.
    window.addEventListener('click', event => {
      const trigger = event.target.closest('.report-content .detail-button, .report-content .row-open');
      const section = trigger?.closest(DOCK_PANELS.map(id => '#' + id).join(','));
      if (!section) return;
      const row = trigger.closest('tr'); selectRow(row);
      const path = row?.querySelector('.row-open[title], code.path[title], [data-copy-path]');
      const value = path?.getAttribute('title') || path?.dataset.copyPath;
      // The snapshot page resolves ?object= to the exact saved record and opens its evidence.
      const name = (row.querySelector('.row-open')?.textContent || '').trim();
      objectLink = value ? location.pathname + '?' + new URLSearchParams({browse: '1', view: section.id === 'all-vms' ? 'vms' : 'inventory', object: value, ...(name ? {name: name + ' — Evidence & notes'} : {})}) : null;
      if (dialog.classList.contains('is-docked')) { newTab.hidden = !objectLink; if (objectLink) newTab.href = objectLink; }
    }, true);
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !dialog.classList.contains('is-docked') || !dialog.open) return;
      if (document.querySelector('dialog[open]:modal')) return;
      event.preventDefault();
      if (mode === 'full') { setMode(previous); return; }
      dialog.close();
    });
    window.addEventListener('hashchange', () => { if (dialog.classList.contains('is-docked') && dialog.open && !dockable()) dialog.close(); });
    window.matchMedia?.('(min-width: 1280px)').addEventListener?.('change', () => { if (dialog.classList.contains('is-docked') && dialog.open && !wide()) dialog.close(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
