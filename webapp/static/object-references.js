/* References page: every saved firewall rule that names one group or custom service.
   Saved snapshot records only; exports use the shared control and never refresh NSX. */
(() => {
  const make = (tag, text, className) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (className) node.className = className; return node; };
  const KINDS = [['group', 'Groups'], ['custom_service', 'Services']];
  function openRecord(dataset, title) {
    const button = make('button'); button.className = 'detail-button'; button.hidden = true; button.dataset.title = title;
    Object.assign(button.dataset, dataset); document.querySelector('main').append(button); button.click(); button.remove();
  }
  async function openPath(path, label) {
    const record = await window.reportData(null, {op: 'resolve', path});
    openRecord({evidenceView: record.view, [record.view === 'tags' ? 'tagRow' : 'evidenceRow']: record.id}, label + ' — Evidence & notes');
  }
  // One row per rule and field where the object is named; excluded fields read NOT.
  function rowsFor(items, object) {
    const rows = [];
    for (const item of items) {
      const matches = item.field_matches?.length ? item.field_matches : [{field: 'Not retained', group: null, excluded: false}];
      for (const match of matches) {
        const direct = !match.group || match.group.path === object.path;
        const field = match.field.replace(' (excluded)', '');
        rows.push({item, field, excluded: !!match.excluded, how: match.group ? (direct ? (match.field.startsWith('Applied to (policy)') ? 'Policy Applied To; applies to every rule in the policy' : 'Named directly' + (match.excluded ? ', excluded' : '')) : 'Through nested group ' + match.group.name) : (item.field_note || item.reference_reason || 'Field not retained')});
      }
    }
    return rows;
  }
  function build(root) {
    const tabs = make('div', null, 'rule-order-modes'); tabs.setAttribute('role', 'group'); tabs.setAttribute('aria-label', 'Object type');
    const picker = make('div', null, 'reference-picker'), search = make('input'); search.type = 'search'; search.placeholder = 'Find a group by name or path'; search.setAttribute('aria-label', 'Find an object');
    const suggestions = make('ul', null, 'reference-suggestions'); suggestions.setAttribute('aria-label', 'Matching objects');
    picker.append(search, suggestions);
    const board = make('div', null, 'reference-board'), main = make('div', null, 'reference-main'), aside = make('aside', null, 'content-card reference-export'); aside.setAttribute('aria-label', 'Export');
    board.append(main, aside);
    root.append(tabs, picker, board);
    let kind = 'group', token = 0, timer, object = null, field = '';
    for (const [value, label] of KINDS) {
      const button = make('button', label); button.type = 'button'; button.dataset.kind = value;
      button.onclick = () => { kind = value; search.value = ''; search.placeholder = 'Find a ' + (value === 'group' ? 'group' : 'custom service') + ' by name or path'; select(null); find(); paint(); };
      tabs.append(button);
    }
    const paint = () => tabs.querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.kind === kind)));
    async function find() {
      const mine = ++token;
      try {
        const data = await window.reportData(null, {op: 'reference-objects', kind, q: search.value.trim()});
        if (mine !== token) return;
        suggestions.replaceChildren();
        for (const item of data.items) {
          const li = make('li'), button = make('button', null, 'reference-suggestion'); button.type = 'button';
          button.setAttribute('aria-pressed', String(object?.path === item.path));
          button.append(make('span', item.name), make('small', item.usage === 'not_assessed' ? 'Not assessed' : item.references == null ? 'References not recorded' : item.references + ' saved reference' + (item.references === 1 ? '' : 's')));
          button.onclick = () => select(item); li.append(button); suggestions.append(li);
        }
        if (!data.items.length) suggestions.append(make('li', 'No objects match in this snapshot.', 'muted'));
      } catch (error) { suggestions.replaceChildren(make('li', error.message || 'Objects could not be searched.', 'muted')); }
    }
    async function select(item) {
      object = item; field = ''; const mine = ++token;
      suggestions.querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(!!item && button.firstChild.textContent === item.name)));
      aside.replaceChildren(); main.replaceChildren();
      if (!item) { main.append(make('p', 'Choose an object to list the saved rules that name it.', 'muted')); aside.hidden = true; return; }
      aside.hidden = false;
      aside.append(make('h2', 'Export references'), make('p', 'Builds a file from the saved snapshot. It never starts an NSX refresh. Only you can download it; downloads expire after 24 hours.', 'muted small'));
      if (window.referenceExportControl) aside.append(window.referenceExportControl({view: 'inventory', path: item.path},
        [{label: 'Firewall rules', exportSection: 'firewall'}, {label: 'Other objects', exportSection: 'objects'}], () => 0));
      main.append(make('p', 'Loading references…', 'rule-order-status'));
      try {
        const [rules, others] = await Promise.all([
          window.reportData(null, {op: 'object-references', path: item.path, section: 'firewall', page: 0, full: 1}),
          window.reportData(null, {op: 'object-references', path: item.path, section: 'objects', page: 0})]);
        if (mine !== token) return;
        render(rules, others);
      } catch (error) { if (mine === token) main.replaceChildren(make('p', error.message || 'References could not be loaded.', 'rule-order-status')); }
    }
    function render(rules, others) {
      const ORDER = ['Source', 'Destination', 'Service', 'Applied to', 'Applied to (policy)'];
      const rows = rowsFor(rules.items, object), fields = [...new Set(rows.map(row => row.field))].sort((a, b) => (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99));
      const head = make('div', null, 'reference-object'), open = make('button', object.name, 'evidence-object-link'); open.type = 'button';
      open.onclick = () => openPath(object.path, object.name);
      head.append(open, make('small', (kind === 'group' ? 'Group' : 'Custom service') + ' · ' + object.path));
      const chips = make('nav', null, 'inventory-filter-chips'); chips.setAttribute('aria-label', 'Field');
      const table = make('div', null, 'table-scroll rule-order-scroll');
      const draw = () => {
        chips.replaceChildren();
        for (const value of ['', ...fields]) {
          const chip = make('a', value || 'All fields'); chip.href = '#object-references'; const count = rows.filter(row => !value || row.field === value).length;
          chip.append(' ', make('span', String(count))); if (value === field) chip.setAttribute('aria-current', 'page');
          chip.onclick = event => { event.preventDefault(); field = value; draw(); }; chips.append(chip);
        }
        const t = make('table', null, 'rule-order-table reference-table'); t.dataset.serverFooter = 'true';
        const thead = make('thead'), labels = make('tr');
        for (const label of ['Order', 'Rule and policy', 'Field', 'How it is named', 'Action']) { const th = make('th', label); th.scope = 'col'; labels.append(th); }
        thead.append(labels); t.append(make('caption', 'Rules that name ' + object.name, 'visually-hidden'), thead);
        const body = make('tbody'); t.append(body);
        for (const row of rows.filter(entry => !field || entry.field === field)) {
          const tr = make('tr'), cell = content => { const td = make('td'); if (content?.nodeType) td.append(content); else td.textContent = content; tr.append(td); };
          if (row.item.disabled === true) tr.className = 'is-disabled';
          cell(String(row.item.nsx_order?.rule_sequence ?? '—'));
          const name = make('div', null, 'rule-order-name'), link = make('button', row.item.name || 'Unnamed rule', 'evidence-object-link'); link.type = 'button';
          link.onclick = () => openPath(row.item.path, row.item.name || 'Rule');
          name.append(link, make('small', [row.item.nsx_order?.category || row.item.category, row.item.nsx_order?.policy || row.item.policy_name].filter(Boolean).join(' › ') + (row.item.disabled === true ? ' · disabled' : ''))); cell(name);
          const fieldCell = make('span', null, 'reference-field'); if (row.excluded) { const not = make('span', 'NOT', 'pill warning'); not.title = 'Excluded: the rule matches everything except this object'; fieldCell.append(not, ' '); }
          fieldCell.append(row.field); cell(fieldCell);
          cell(row.how); cell(window.evidenceBadge ? window.evidenceBadge(row.item.action, 'action') : row.item.action || 'Not recorded');
          body.append(tr);
        }
        if (!body.children.length) { const tr = make('tr'), td = make('td', 'No saved rule names this object' + (field ? ' in that field.' : '.'), 'empty-state'); td.colSpan = 5; tr.append(td); body.append(tr); }
        table.replaceChildren(t);
      };
      draw();
      const parts = [head, chips, table];
      if (rules.has_next) parts.push(make('p', 'The first 500 referencing rules are shown. The export includes every page.', 'notice'));
      const other = make('section', null, 'content-card reference-others'), title = make('div', null, 'content-card-head'); title.append(make('h2', 'Other referencing objects'));
      const list = make('ul', null, 'overview-rows');
      for (const entry of others.items) { const li = make('li'), link = make('button', entry.name || entry.path, 'evidence-object-link'); link.type = 'button'; link.onclick = () => openPath(entry.path, entry.name || entry.path); li.append(link); list.append(li); }
      if (!others.items.length) list.append(make('li', 'No other saved object references this one.'));
      other.append(title, list); if (others.has_next) other.append(make('p', 'More objects are listed in the export and in the evidence drawer.', 'content-card-note'));
      parts.push(other, make('p', 'NOT marks an excluded Source or Destination: the rule matches everything except this object. A policy Applied To applies to every rule in the policy. Nested references are listed through the group that contains them.', 'rule-order-note'));
      main.replaceChildren(...parts);
    }
    search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(find, 250); });
    paint(); select(null); find();
  }
  function init() {
    const root = document.querySelector('[data-object-references]');
    if (!root || root.dataset.ready || !window.reportData) return;
    root.dataset.ready = 'true'; build(root);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
