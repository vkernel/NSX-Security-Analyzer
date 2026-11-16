/* Firewall rules in configured NSX order, grouped by category and policy.
   Saved configuration only. Cells reuse the reference drawer's field renderer and open the
   same evidence drawer; the sortable table (search, filters, export) stays available. */
(() => {
  const PANEL = 'dfw-rules', KEY = 'rules-layout';
  const CATEGORIES = ['Ethernet', 'Emergency', 'Infrastructure', 'Environment', 'Application'];
  const ACTIONS = [['ALLOW', 'Allow'], ['DROP', 'Drop'], ['REJECT', 'Reject'], ['JUMP_TO_APPLICATION', 'Jump to application']];
  const make = (tag, text, className) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (className) node.className = className; return node; };
  const stored = () => { try { return localStorage.getItem(KEY); } catch (_) { return null; } };
  const store = value => { try { localStorage.setItem(KEY, value); } catch (_) {} };

  // Same path as related-object navigation: resolve the saved record, then use the shared drawer.
  function openRecord(dataset, title) {
    const button = make('button'); button.className = 'detail-button'; button.hidden = true; button.dataset.title = title;
    Object.assign(button.dataset, dataset);
    document.querySelector('main').append(button); button.click(); button.remove();
  }
  async function openPath(path, label) {
    try {
      const record = await window.reportData(null, {op: 'resolve', path});
      openRecord({evidenceView: record.view, [record.view === 'tags' ? 'tagRow' : 'evidenceRow']: record.id}, label + ' — Evidence & notes');
    } catch (error) { status.textContent = error.message || 'This object could not be opened.'; }
  }
  let status, pendingPolicy = null;

  function fieldCell(field, none) {
    const cell = window.firewallRuleFieldCell(document.getElementById('detail-body') || document.body, field, {none});
    cell.querySelectorAll('button.evidence-object-link').forEach(link => { link.onclick = () => openPath(link.title, link.textContent); });
    return cell;
  }

  function build(section) {
    const root = make('div', null, 'rule-order'); root.dataset.ruleOrder = '';
    const modes = make('div', null, 'rule-order-modes'); modes.setAttribute('role', 'group'); modes.setAttribute('aria-label', 'Rule list layout');
    const ordered = make('button', 'NSX order'), sortable = make('button', 'Sortable table');
    ordered.type = sortable.type = 'button'; modes.append(ordered, sortable);
    const tools = make('form', null, 'rule-order-tools'); tools.setAttribute('role', 'search');
    const search = make('input'); search.type = 'search'; search.placeholder = 'Rule name, ID or path'; search.setAttribute('aria-label', 'Filter rules');
    const select = (label, options) => { const node = make('select'); node.setAttribute('aria-label', label); node.add(new Option(label + ': All', '')); for (const [value, text] of options) node.add(new Option(text, value)); return node; };
    const category = select('Category', CATEGORIES.map(name => [name, name])), action = select('Action', ACTIONS);
    status = make('span', '', 'rule-order-status'); status.setAttribute('role', 'status');
    const policyChip = make('button', null, 'rule-order-policy'); policyChip.type = 'button'; policyChip.hidden = true;
    let policy = null;
    tools.append(search, category, action, policyChip, status);
    const views = make('div', null, 'rule-order-views');
    const scroll = make('div', null, 'table-scroll rule-order-scroll'); scroll.tabIndex = 0; scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', 'Rules in NSX order');
    const pager = make('nav', null, 'rule-order-pager'); pager.setAttribute('aria-label', 'Rule pages');
    const range = make('span'), previous = make('button', 'Previous'), next = make('button', 'Next');
    previous.type = next.type = 'button'; pager.append(range, previous, next);
    const note = make('p', 'Configured NSX order: category, policy sequence, rule sequence. NOT marks an excluded Source or Destination; "From policy" marks an Applied To inherited from the policy. This is saved configuration, not effective policy or traffic.', 'rule-order-note');
    const body = make('div', null, 'rule-order-body'); body.append(tools, views, scroll, pager, note);
    root.append(modes, body);

    let page = 0, token = 0, timer;
    async function load(target = 0) {
      const mine = ++token; root.setAttribute('aria-busy', 'true'); status.textContent = 'Loading rules…';
      try {
        const data = await window.reportData(null, {op: 'rule-order', page: target, q: search.value.trim(), category: category.value, action: action.value, policy: policy?.path || ''});
        if (mine !== token) return;
        page = target; render(data);
      } catch (error) {
        if (mine === token) status.textContent = error.message || 'Rules could not be loaded.';
      } finally { if (mine === token) root.removeAttribute('aria-busy'); }
    }
    function render(data) {
      const profiles = data.items.some(item => item.show_context_profiles);
      const columns = ['Order', 'Rule', 'Source', 'Destination', 'Services', ...(profiles ? ['Context profiles'] : []), 'Applied To', 'Action', 'State'];
      const table = make('table', null, 'rule-order-table'); table.dataset.serverFooter = 'true';
      const caption = make('caption', 'Firewall rules in NSX order'), head = make('thead'), labels = make('tr');
      caption.className = 'visually-hidden';
      for (const label of columns) { const th = make('th', label); th.scope = 'col'; labels.append(th); }
      head.append(labels); table.append(caption, head);
      let group = null, previousItem = null;
      for (const item of data.items) {
        const order = item.nsx_order || {}, before = previousItem?.nsx_order;
        const sameCategory = !!before && before.category === order.category, samePolicy = sameCategory && before.policy_key === order.policy_key;
        if (!group || !samePolicy) { group = make('tbody'); group.className = 'rule-order-group'; table.append(group); }
        const heading = (level, title, details) => {
          const tr = make('tr', null, 'rule-order-heading'), th = make('th'); tr.dataset.level = level; th.scope = 'rowgroup'; th.colSpan = columns.length;
          th.append(make('strong', title)); for (const detail of details.filter(Boolean)) th.append(make('small', detail)); tr.append(th); group.append(tr);
        };
        const count = (value, unit) => value + ' ' + unit + (value === 1 ? '' : 's');
        if (!sameCategory) heading('category', order.category_known ? order.category : 'Category: ' + (order.category || 'Not recorded'), [count(order.category_count, 'rule'), !before && order.category_continued ? 'continued from the previous page' : '']);
        if (!samePolicy) heading('policy', order.policy || 'Policy not recorded', [order.policy_sequence != null ? 'Policy sequence ' + order.policy_sequence : 'Order not recorded', count(order.policy_count, 'rule'), !before && order.policy_continued ? 'continued from the previous page' : '']);
        previousItem = item;
        const row = make('tr'); if (item.disabled === true) row.className = 'is-disabled';
        const cell = content => { const td = make('td'); if (content?.nodeType) td.append(content); else td.textContent = content == null ? 'Not recorded' : String(content); row.append(td); };
        cell(order.rule_sequence ?? 'Not recorded');
        const name = make('div', null, 'rule-order-name'), open = make('button', item.name || 'Unnamed rule', 'evidence-object-link');
        open.type = 'button'; open.title = item.path || ''; open.setAttribute('aria-label', 'Inspect ' + (item.name || 'rule'));
        open.onclick = () => openRecord({evidenceRow: item.ordinal, evidenceView: 'dfw'}, (item.name || 'Rule') + ' — Evidence & notes');
        name.append(open); if (item.rule_id != null) name.append(make('small', 'ID ' + item.rule_id)); cell(name);
        const fields = item.rule_fields || {};
        cell(fieldCell(fields.sources, 'Any')); cell(fieldCell(fields.destinations, 'Any')); cell(fieldCell(fields.services, 'Any'));
        if (profiles) cell(fieldCell(fields.context_profiles, 'None'));
        cell(fieldCell(fields.applied_to, 'DFW'));
        cell(window.evidenceBadge(item.action, 'action'));
        cell(window.evidenceBadge(item.disabled === true ? 'Disabled' : item.disabled === false ? 'Enabled' : null));
        group.append(row);
      }
      if (!data.items.length) { const body = make('tbody'), tr = make('tr'), td = make('td', 'No rules match these filters in this snapshot.', 'empty-state'); td.colSpan = columns.length; tr.append(td); body.append(tr); table.append(body); }
      scroll.replaceChildren(table);
      const start = data.items.length ? data.page * data.page_size + 1 : 0;
      range.textContent = data.items.length ? `Showing ${start}–${start + data.items.length - 1} of ${data.total}` : '';
      status.textContent = data.total + ' rule' + (data.total === 1 ? '' : 's') + (search.value || category.value || action.value || policy ? ' match' : ' in this snapshot');
      previous.disabled = !data.page; next.disabled = !data.has_next; pager.hidden = !data.page && !data.has_next;
    }
    previous.onclick = () => load(Math.max(0, page - 1));
    next.onclick = () => load(page + 1);
    tools.addEventListener('submit', event => { event.preventDefault(); load(0); });
    search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => load(0), 300); });
    category.addEventListener('change', () => load(0)); action.addEventListener('change', () => load(0));
    policyChip.onclick = () => { policy = null; policyChip.hidden = true; load(0); search.focus(); };
    // Policies page "Open in Rules": show only that policy's rules, in order.
    window.ruleOrderShowPolicy = (path, name) => {
      policy = {path, name}; policyChip.textContent = 'Policy: ' + name + ' ×'; policyChip.setAttribute('aria-label', 'Remove policy filter ' + name); policyChip.hidden = false;
      search.value = ''; category.value = ''; action.value = '';
      store('nsx'); setMode('nsx'); loaded = true; load(0);
    };

    let loaded = false;
    function setMode(mode) {
      const nsx = mode !== 'table';
      ordered.setAttribute('aria-pressed', String(nsx)); sortable.setAttribute('aria-pressed', String(!nsx));
      body.hidden = !nsx; section.classList.toggle('rule-order-active', nsx);
      placeViews();
      if (nsx && !loaded) { loaded = true; load(0); }
    }
    // The view chips live in the sortable table's toolbar; show them above the ordered list too.
    function placeViews() {
      const slot = document.querySelector('.report-view-selector');
      if (!slot || location.hash.slice(1) !== PANEL) return;
      if (section.classList.contains('rule-order-active')) views.append(slot);
      else section.querySelector('.table-tools')?.append(slot);
    }
    ordered.onclick = () => { store('nsx'); setMode('nsx'); };
    sortable.onclick = () => { store('table'); setMode('table'); };
    document.addEventListener('report-navigation-ready', placeViews);
    const widget = section.querySelector(':scope > .table-widget');
    if (widget) widget.before(root); else section.append(root);
    setMode(stored() === 'table' ? 'table' : 'nsx');
    // The section loads after navigation placed the view chips in the sortable toolbar.
    setTimeout(placeViews, 0);
    if (pendingPolicy) { const wanted = pendingPolicy; pendingPolicy = null; window.ruleOrderShowPolicy(wanted.path, wanted.name); }
  }

  function buildPolicies(section) {
    const root = make('div', null, 'rule-order policy-order'); root.dataset.policyOrder = '';
    const modes = make('div', null, 'rule-order-modes'); modes.setAttribute('role', 'group'); modes.setAttribute('aria-label', 'Policy list layout');
    const ordered = make('button', 'NSX order'), sortable = make('button', 'Sortable table'); ordered.type = sortable.type = 'button'; modes.append(ordered, sortable);
    const board = make('div', null, 'policy-board'), scroll = make('div', null, 'table-scroll rule-order-scroll');
    scroll.tabIndex = 0; scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', 'Policies in NSX order');
    const aside = make('aside', null, 'policy-detail content-card'); aside.setAttribute('aria-label', 'Policy detail'); aside.tabIndex = -1; aside.hidden = true;
    const state = make('p', '', 'rule-order-status'); state.setAttribute('role', 'status');
    const note = make('p', 'A rule with its own Applied To is shown as such; a policy Applied To applies to every rule in the policy. Disabled rules are listed but greyed out. Saved configuration, not effective policy.', 'rule-order-note');
    const views = make('div', null, 'rule-order-views');
    const body = make('div', null, 'rule-order-body'); board.append(scroll, aside); body.append(views, state, board, note); root.append(modes, body);
    let selected = null, loaded = false;
    const scopeCell = scope => {
      const node = make('div', null, 'evidence-rule-field');
      if (!scope?.recorded) node.append(make('span', 'Not recorded'));
      else if (scope.any) node.append(make('span', 'DFW'));
      else for (const entry of scope.items.slice(0, 3)) {
        const link = make('button', entry.name, 'evidence-object-link'); link.type = 'button'; link.title = entry.path; link.onclick = () => openPath(entry.path, entry.name); node.append(link);
      }
      if (scope?.items?.length > 3) node.append(make('small', '+' + (scope.items.length - 3) + ' more'));
      return node;
    };
    const flags = item => {
      const node = make('span', null, 'policy-flags'), add = (text, tone) => { const pill = make('span', text, 'pill ' + tone); node.append(pill); };
      if (!item.saved_rules) add('No rules', 'warning');
      if (item.disabled_rules) add(item.disabled_rules + ' disabled', 'neutral');
      if (item.status && !['has_rules', 'empty'].includes(item.status)) add('Inventory ' + String(item.status).replaceAll('_', ' '), 'warning');
      if (item.rule_count != null && item.rule_count !== item.saved_rules) add('Rule count differs', 'warning');
      return node;
    };
    async function showDetail(item, row) {
      selected = item.path;
      scroll.querySelectorAll('tr[aria-current]').forEach(node => node.removeAttribute('aria-current')); row.setAttribute('aria-current', 'true');
      aside.hidden = false; aside.replaceChildren();
      const head = make('div', null, 'policy-detail-head');
      head.append(make('small', (item.category || 'Category not recorded') + ' · ' + (item.sequence != null ? 'sequence ' + item.sequence : 'sequence not recorded')), make('h2', item.name));
      const facts = make('dl', null, 'review-facts'), fact = (label, value) => { const dd = make('dd'); if (value?.nodeType) dd.append(value); else dd.textContent = value; facts.append(make('dt', label), dd); };
      fact('Applied To', scopeCell(item.applied_to));
      fact('Stateful', item.stateful === true ? 'Yes' : item.stateful === false ? 'No' : 'Not recorded');
      fact('Rules', item.saved_rules + ' saved' + (item.disabled_rules ? ' · ' + item.disabled_rules + ' disabled' : ''));
      const actions = make('div', null, 'review-detail-actions'), inspect = make('button', 'Inspect evidence', 'button'), open = make('button', 'Open in Rules', 'button');
      inspect.type = open.type = 'button';
      inspect.onclick = () => openRecord({evidenceRow: item.ordinal, evidenceView: 'dfw'}, item.name + ' — Evidence & notes');
      open.onclick = () => {
        // The Rules section may still be loading; it applies the policy filter once built.
        if (window.ruleOrderShowPolicy) setTimeout(() => window.ruleOrderShowPolicy(item.path, item.name), 0); else pendingPolicy = {path: item.path, name: item.name};
        location.hash = '#' + PANEL;
      };
      actions.append(open, inspect);
      const list = make('div', null, 'policy-rules'); list.append(make('h3', 'Rules in order'));
      const rules = make('ol'); list.append(rules); rules.append(make('li', 'Loading rules…'));
      aside.append(head, facts, list, actions);
      if (!window.matchMedia || !window.matchMedia('(min-width: 1100px)').matches) aside.scrollIntoView({block: 'start'});
      aside.focus({preventScroll: true});
      try {
        const data = await window.reportData(null, {op: 'rule-order', policy: item.path, page: 0});
        if (selected !== item.path) return;
        rules.replaceChildren();
        for (const rule of data.items) {
          const li = make('li'); if (rule.disabled === true) li.className = 'is-disabled';
          const link = make('button', rule.name || 'Unnamed rule', 'evidence-object-link'); link.type = 'button';
          link.onclick = () => openRecord({evidenceRow: rule.ordinal, evidenceView: 'dfw'}, (rule.name || 'Rule') + ' — Evidence & notes');
          li.append(make('span', rule.nsx_order?.rule_sequence ?? '—', 'policy-rule-order'), link, window.evidenceBadge(rule.action, 'action'));
          if (rule.disabled === true) li.append(make('small', 'Disabled'));
          rules.append(li);
        }
        if (!data.items.length) rules.append(make('li', 'No rules saved for this policy.'));
        if (data.has_next) rules.append(make('li', 'More rules: use Open in Rules.'));
      } catch (error) { if (selected === item.path) rules.replaceChildren(make('li', error.message || 'Rules could not be loaded.')); }
    }
    async function load() {
      state.textContent = 'Loading policies…';
      try {
        const data = await window.reportData(null, {op: 'policy-order', page: 0});
        const table = make('table', null, 'rule-order-table policy-order-table'); table.dataset.serverFooter = 'true';
        const head = make('thead'), labels = make('tr');
        for (const label of ['Seq', 'Policy', 'Applied To', 'Rules', 'Attention']) { const th = make('th', label); th.scope = 'col'; labels.append(th); }
        head.append(labels); table.append(make('caption', 'Firewall policies in NSX order'), head); table.querySelector('caption').className = 'visually-hidden';
        let group = null, category = null;
        for (const item of data.items) {
          if (item.category !== category) {
            category = item.category; group = make('tbody'); table.append(group);
            const tr = make('tr', null, 'rule-order-heading'), th = make('th'); tr.dataset.level = 'category'; th.scope = 'rowgroup'; th.colSpan = 5;
            th.append(make('strong', item.category_known ? item.category : 'Category: ' + item.category), make('small', item.category_count + ' polic' + (item.category_count === 1 ? 'y' : 'ies'))); tr.append(th); group.append(tr);
          }
          const row = make('tr'), cell = content => { const td = make('td'); if (content?.nodeType) td.append(content); else td.textContent = content; row.append(td); };
          cell(item.sequence ?? '—');
          const name = make('div', null, 'rule-order-name'), pick = make('button', item.name, 'evidence-object-link'); pick.type = 'button'; pick.setAttribute('aria-controls', 'policy-detail');
          pick.onclick = () => showDetail(item, row); name.append(pick, make('small', item.stateful === false ? 'Stateless' : item.stateful === true ? 'Stateful' : 'Stateful not recorded')); cell(name);
          cell(scopeCell(item.applied_to)); cell(String(item.saved_rules)); cell(flags(item));
          group.append(row);
          if (item.path === selected) row.setAttribute('aria-current', 'true');
        }
        scroll.replaceChildren(table);
        state.textContent = data.items.length + ' polic' + (data.items.length === 1 ? 'y' : 'ies') + ' in NSX category order' + (data.has_next ? ' · first ' + data.page_size + ' shown' : '');
      } catch (error) { state.textContent = error.message || 'Policies could not be loaded.'; }
    }
    aside.id = 'policy-detail';
    function setMode(mode) {
      const nsx = mode !== 'table';
      ordered.setAttribute('aria-pressed', String(nsx)); sortable.setAttribute('aria-pressed', String(!nsx));
      body.hidden = !nsx; section.classList.toggle('rule-order-active', nsx); placeViews();
      if (nsx && !loaded) { loaded = true; load(); }
    }
    function placeViews() {
      const slot = document.querySelector('.report-view-selector');
      if (!slot || location.hash.slice(1) !== 'dfw-policies') return;
      if (section.classList.contains('rule-order-active')) views.append(slot); else section.querySelector('.table-tools')?.append(slot);
    }
    ordered.onclick = () => { store('nsx'); setMode('nsx'); };
    sortable.onclick = () => { store('table'); setMode('table'); };
    document.addEventListener('report-navigation-ready', placeViews);
    const widget = section.querySelector(':scope > .table-widget');
    if (widget) widget.before(root); else section.append(root);
    setMode(stored() === 'table' ? 'table' : 'nsx');
    setTimeout(placeViews, 0);
  }

  function init() {
    const policies = document.getElementById('dfw-policies');
    if (policies && !policies.dataset.lazyPanel && !policies.querySelector('[data-policy-order]') && window.reportData && window.evidenceBadge) buildPolicies(policies);
    const section = document.getElementById(PANEL);
    // Lazy report sections are filled later; build once the saved section markup is present.
    if (!section || section.dataset.lazyPanel || section.querySelector('[data-rule-order]') || !window.reportData || !window.firewallRuleFieldCell) return;
    build(section);
  }
  document.addEventListener('report-section-loaded', init);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
