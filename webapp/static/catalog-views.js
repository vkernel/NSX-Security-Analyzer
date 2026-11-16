/* Services and Tags & scopes overview layouts. Saved snapshot records only; names open the
   shared evidence drawer and the sortable tables (search, filters, export) stay available. */
(() => {
  const make = (tag, text, className) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (className) node.className = className; return node; };
  const stored = key => { try { return localStorage.getItem(key); } catch (_) { return null; } };
  const store = (key, value) => { try { localStorage.setItem(key, value); } catch (_) {} };
  const number = value => value == null ? '—' : String(value);
  function openRecord(dataset, title) {
    const button = make('button'); button.className = 'detail-button'; button.hidden = true; button.dataset.title = title;
    Object.assign(button.dataset, dataset); document.querySelector('main').append(button); button.click(); button.remove();
  }
  const pill = (text, tone) => make('span', text, 'pill ' + tone);
  const TONES = {'Not referenced': 'warning', 'Broad port range': 'warning', 'Nested service': 'neutral', 'No VMs in snapshot': 'warning', 'VM tags only': 'neutral', 'Needs review': 'warning'};

  // Shared frame: layout switch, body and the report's view chips above the overview.
  function frame(section, {key, label, overview}) {
    const root = make('div', null, 'rule-order catalog-view'); root.dataset.catalogView = section.id;
    const modes = make('div', null, 'rule-order-modes'); modes.setAttribute('role', 'group'); modes.setAttribute('aria-label', label);
    const first = make('button', 'Overview'), table = make('button', 'Sortable table'); first.type = table.type = 'button'; modes.append(first, table);
    const views = make('div', null, 'rule-order-views'), body = make('div', null, 'rule-order-body'); body.append(views);
    root.append(modes, body);
    let loaded = false;
    const place = () => {
      const slot = document.querySelector('.report-view-selector');
      if (!slot || location.hash.slice(1) !== section.id) return;
      if (section.classList.contains('rule-order-active')) views.append(slot); else section.querySelector('.table-tools')?.append(slot);
    };
    const set = mode => {
      const on = mode !== 'table';
      first.setAttribute('aria-pressed', String(on)); table.setAttribute('aria-pressed', String(!on));
      body.hidden = !on; section.classList.toggle('rule-order-active', on); place();
      if (on && !loaded) { loaded = true; overview(body); }
    };
    first.onclick = () => { store(key, 'overview'); set('overview'); };
    table.onclick = () => { store(key, 'table'); set('table'); };
    document.addEventListener('report-navigation-ready', place);
    const widget = section.querySelector(':scope > .table-widget');
    if (widget) widget.before(root); else section.append(root);
    set(stored(key) === 'table' ? 'table' : 'overview');
    setTimeout(place, 0);
  }

  function services(body) {
    const cards = make('div', null, 'catalog-cards'), tools = make('form', null, 'rule-order-tools');
    const search = make('input'); search.type = 'search'; search.placeholder = 'Name, port or protocol'; search.setAttribute('aria-label', 'Filter services');
    const status = make('span', '', 'rule-order-status'); status.setAttribute('role', 'status'); tools.append(search, status);
    const scroll = make('div', null, 'table-scroll rule-order-scroll'); scroll.tabIndex = 0; scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', 'Services');
    const pager = make('nav', null, 'rule-order-pager'), range = make('span'), previous = make('button', 'Previous'), next = make('button', 'Next');
    pager.setAttribute('aria-label', 'Service pages'); previous.type = next.type = 'button'; pager.append(range, previous, next);
    const note = make('p', 'Nested services are listed by reference; their entries are not expanded. "Not referenced" means no saved rule or object uses the service in this snapshot, not that it is unused elsewhere. Built-in services are not assessed.', 'rule-order-note');
    body.append(cards, tools, scroll, pager, note);
    let view = '', page = 0, token = 0, timer;
    async function load(target = 0) {
      const mine = ++token; status.textContent = 'Loading services…';
      try {
        const data = await window.reportData(null, {op: 'service-list', page: target, view, q: search.value.trim()});
        if (mine !== token) return; page = target; render(data);
      } catch (error) { if (mine === token) status.textContent = error.message || 'Services could not be loaded.'; }
    }
    function render(data) {
      cards.replaceChildren();
      for (const [key, label, value, hint] of [['', 'All services', data.totals.total, 'In this snapshot'], ['custom', 'Custom', data.totals.custom, 'Assessed for references'],
        ['predefined', 'Predefined', data.totals.predefined, 'Not assessed'], ['unreferenced', 'Not referenced', data.totals.unreferenced, 'No saved reference']]) {
        const card = make('button', null, 'kpi-card'); card.type = 'button'; card.setAttribute('aria-pressed', String(view === key));
        card.append(make('span', label), make('strong', number(value)), make('small', hint));
        card.onclick = () => { view = key; load(0); }; cards.append(card);
      }
      const table = make('table', null, 'rule-order-table catalog-table'); table.dataset.serverFooter = 'true';
      const head = make('thead'), labels = make('tr');
      for (const label of ['Service', 'Entries', 'Type', 'Rules', 'Observation']) { const th = make('th', label); th.scope = 'col'; labels.append(th); }
      head.append(labels); const caption = make('caption', 'Services', 'visually-hidden'); table.append(caption, head);
      const rows = make('tbody'); table.append(rows);
      for (const item of data.items) {
        const row = make('tr'), cell = content => { const td = make('td'); if (content?.nodeType) td.append(content); else td.textContent = content; row.append(td); };
        const name = make('div', null, 'rule-order-name'), open = make('button', item.name, 'evidence-object-link'); open.type = 'button';
        open.onclick = () => openRecord({evidenceRow: item.ordinal, evidenceView: 'inventory'}, item.name + ' — Evidence & notes');
        name.append(open, make('small', item.path)); cell(name);
        cell(item.entries.length ? item.entries.join(', ') : 'Not recorded');
        cell(item.predefined ? 'Predefined' : 'Custom');
        cell(item.rules == null ? 'Not assessed' : String(item.rules));
        const notes = make('span', null, 'policy-flags'); for (const text of item.notes) notes.append(pill(text, TONES[text] || 'neutral'));
        if (!item.notes.length) notes.append(make('span', '—', 'muted')); cell(notes);
        rows.append(row);
      }
      if (!data.items.length) { const tr = make('tr'), td = make('td', 'No services match.', 'empty-state'); td.colSpan = 5; tr.append(td); rows.append(tr); }
      scroll.replaceChildren(table);
      const start = data.page * data.page_size + 1;
      range.textContent = data.items.length ? `Showing ${start}–${start + data.items.length - 1} of ${data.count}` : '';
      status.textContent = data.count + ' shown';
      previous.disabled = !data.page; next.disabled = !data.has_next; pager.hidden = !data.page && !data.has_next;
    }
    previous.onclick = () => load(Math.max(0, page - 1)); next.onclick = () => load(page + 1);
    tools.addEventListener('submit', event => { event.preventDefault(); load(0); });
    search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => load(0), 300); });
    load(0);
  }

  function tags(body) {
    const board = make('div', null, 'scope-board'), side = make('section', null, 'content-card scope-list'), main = make('div', null, 'scope-main');
    side.setAttribute('aria-label', 'Scopes');
    const filter = make('input'); filter.type = 'search'; filter.placeholder = 'Filter scopes'; filter.setAttribute('aria-label', 'Filter scopes');
    const list = make('ul'); list.setAttribute('aria-label', 'Scopes'); side.append(filter, list);
    const note = make('p', 'A shared tag alone does not make a VM a group member. Values are case-sensitive in NSX; values that differ only in case are flagged.', 'rule-order-note');
    board.append(side, main); body.append(board, note);
    let scopes = [], selected = null, token = 0;
    function renderList() {
      const term = filter.value.trim().toLowerCase(); list.replaceChildren();
      for (const scope of scopes.filter(entry => !term || entry.name.toLowerCase().includes(term))) {
        const li = make('li'), button = make('button'); button.type = 'button'; button.setAttribute('aria-pressed', String(scope.name === selected));
        button.append(make('span', scope.name || '(no scope)'), make('small', number(scope.values) + ' value' + (scope.values === 1 ? '' : 's')));
        button.onclick = () => pick(scope.name); li.append(button); list.append(li);
      }
      if (!list.children.length) list.append(make('li', 'No scopes match.', 'muted'));
    }
    async function pick(name) {
      selected = name; renderList(); const mine = ++token;
      main.replaceChildren(make('p', 'Loading values…', 'rule-order-status'));
      try {
        const data = await window.reportData(null, {op: 'tag-scopes', scope: name});
        if (mine !== token) return;
        const scope = scopes.find(entry => entry.name === name) || {};
        const stats = make('div', null, 'kpi-grid operational-summary catalog-stats');
        for (const [label, value] of [['Values', scope.values], ['Tagged VMs', scope.vms], ['Group definitions', scope.groups], ['Other objects', scope.other]]) {
          const card = make('div', null, 'kpi-card'); card.append(make('span', label), make('strong', number(value))); stats.append(card);
        }
        const card = make('section', null, 'content-card'), head = make('div', null, 'content-card-head');
        head.append(make('h2', 'Values in ' + (name || '(no scope)')), make('span', 'Case-sensitive', 'muted small'));
        const scroll = make('div', null, 'table-scroll'), table = make('table', null, 'rule-order-table catalog-table'); table.dataset.serverFooter = 'true';
        const thead = make('thead'), labels = make('tr');
        for (const label of ['Value', 'VMs', 'Group definitions', 'Other', 'Notes']) { const th = make('th', label); th.scope = 'col'; labels.append(th); }
        thead.append(labels); table.append(make('caption', 'Values in ' + name, 'visually-hidden'), thead);
        const rows = make('tbody'); table.append(rows);
        for (const item of data.values) {
          const row = make('tr'), cell = content => { const td = make('td'); if (content?.nodeType) td.append(content); else td.textContent = content; row.append(td); };
          const open = make('button', item.value, 'evidence-object-link'); open.type = 'button';
          open.onclick = () => openRecord({tagRow: item.ordinal, evidenceView: 'tags'}, name + ': ' + item.value + ' — Relationships');
          cell(open); cell(number(item.vms)); cell(number(item.groups)); cell(number(item.other));
          const notes = make('span', null, 'policy-flags'); for (const text of item.notes) notes.append(pill(text, TONES[text] || (text.startsWith('Case') ? 'warning' : 'neutral')));
          if (!item.notes.length) notes.append(make('span', '—', 'muted')); cell(notes); rows.append(row);
        }
        if (!data.values.length) { const tr = make('tr'), td = make('td', 'No values saved for this scope.', 'empty-state'); td.colSpan = 5; tr.append(td); rows.append(tr); }
        scroll.append(table); card.append(head, scroll);
        if (data.truncated) card.append(make('p', 'First 1,000 values shown. Use the sortable table to search all values.', 'content-card-note'));
        main.replaceChildren(stats, card);
      } catch (error) { if (mine === token) main.replaceChildren(make('p', error.message || 'Values could not be loaded.', 'rule-order-status')); }
    }
    filter.addEventListener('input', renderList);
    (async () => {
      try {
        scopes = (await window.reportData(null, {op: 'tag-scopes'})).scopes;
        renderList(); if (scopes.length) pick(scopes[0].name); else main.replaceChildren(make('p', 'No tag scopes were saved in this snapshot.', 'muted'));
      } catch (error) { main.replaceChildren(make('p', error.message || 'Scopes could not be loaded.', 'rule-order-status')); }
    })();
  }

  function init() {
    if (!window.reportData) return;
    const service = document.getElementById('all-services'), scope = document.getElementById('tags-scopes');
    if (service && !service.dataset.lazyPanel && !service.querySelector('[data-catalog-view]')) frame(service, {key: 'services-layout', label: 'Service list layout', overview: services});
    if (scope && !scope.dataset.lazyPanel && !scope.querySelector('[data-catalog-view]')) frame(scope, {key: 'scopes-layout', label: 'Scope list layout', overview: tags});
  }
  document.addEventListener('report-section-loaded', init);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
