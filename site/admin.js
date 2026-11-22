/* PaperLeaf Books admin dashboard: tickets, feedback and basic analytics.
 * Vanilla JS, same conventions as chat.js: DOM built with createElement/textContent only (no innerHTML). */
(() => {
  'use strict';

  const root = document.getElementById('admin-root');
  const state = { tab: 'tickets', tickets: [], feedback: [], analytics: null };

  function h(tag, props = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === false || value == null) continue;
      if (key === 'class') el.className = value;
      else if (key === 'text') el.textContent = value;
      else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
      else if (value === true) el.setAttribute(key, '');
      else el.setAttribute(key, value);
    }
    for (const child of children.flat()) if (child != null && child !== false) el.append(child);
    return el;
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, { credentials: 'same-origin', ...opts });
    let data = null;
    try { data = await res.json(); } catch { /* not JSON */ }
    if (res.status === 401) { showLogin(); throw { status: 401 }; }
    if (!res.ok) throw { status: res.status, message: (data && data.detail) || 'Something went wrong.' };
    return data;
  }

  // ---------- login ----------
  function showLogin(error) {
    const passcode = h('input', { type: 'password', autocomplete: 'current-password', required: true });
    const errorEl = h('p', { class: 'admin-error' }, error || '');
    const form = h(
      'form',
      {
        class: 'admin-login',
        onsubmit: async (e) => {
          e.preventDefault();
          try {
            await api('/api/admin/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ passcode: passcode.value }) });
            showDashboard();
          } catch (err) {
            errorEl.textContent = err.message || 'Incorrect passcode.';
          }
        },
      },
      h('h1', { text: 'Admin login' }),
      errorEl,
      h('label', {}, 'Passcode', passcode),
      h('button', { class: 'btn btn-primary', type: 'submit' }, 'Log in'),
    );
    root.replaceChildren(form);
    passcode.focus();
  }

  // ---------- dashboard shell ----------
  async function logout() {
    try { await api('/api/admin/logout', { method: 'POST' }); } catch { /* ignore */ }
    showLogin();
  }

  function tabButton(id, label) {
    return h('button', {
      class: 'admin-tab', type: 'button', role: 'tab', 'aria-selected': String(state.tab === id),
      onclick: () => { state.tab = id; renderTab(); },
    }, label);
  }

  async function showDashboard() {
    const shell = h(
      'div', { class: 'admin-shell' },
      h('div', { class: 'admin-header' },
        h('h1', { text: 'PaperLeaf Books – Admin' }),
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: logout }, 'Log out')),
      h('div', { class: 'admin-tabs', role: 'tablist' }, tabButton('tickets', 'Tickets'), tabButton('feedback', 'Feedback'), tabButton('analytics', 'Analytics')),
      h('div', { id: 'admin-tab-body' }),
    );
    root.replaceChildren(shell);
    await renderTab();
  }

  async function renderTab() {
    document.querySelectorAll('.admin-tab').forEach((b, i) => b.setAttribute('aria-selected', String(['tickets', 'feedback', 'analytics'][i] === state.tab)));
    const body = document.getElementById('admin-tab-body');
    body.replaceChildren(h('p', { class: 'admin-empty', text: 'Loading…' }));
    try {
      if (state.tab === 'tickets') body.replaceChildren(await ticketsView());
      else if (state.tab === 'feedback') body.replaceChildren(await feedbackView());
      else body.replaceChildren(await analyticsView());
    } catch (err) {
      if (err.status !== 401) body.replaceChildren(h('p', { class: 'admin-error', text: err.message || 'Could not load this tab.' }));
    }
  }

  // ---------- tickets ----------
  function table(columns, rows) {
    if (!rows.length) return h('div', { class: 'admin-table-wrap' }, h('p', { class: 'admin-empty', text: 'No results.' }));
    return h(
      'div', { class: 'admin-table-wrap' },
      h('table', { class: 'admin-table' },
        h('thead', {}, h('tr', {}, columns.map((c) => h('th', { text: c[1] })))),
        h('tbody', {}, rows.map((row) => h('tr', {}, columns.map((c) => h('td', { title: String(row[c[0]] ?? '') }, String(row[c[0]] ?? ''))))))),
    );
  }

  async function ticketsView() {
    const status = h('select', {}, ['', 'open', 'closed'].map((v) => h('option', { value: v, text: v || 'Any status' })));
    const priority = h('select', {}, ['', 'normal', 'high'].map((v) => h('option', { value: v, text: v || 'Any priority' })));
    const q = h('input', { type: 'search', placeholder: 'Search description, name, email, id…' });
    const resultsWrap = h('div');

    async function load() {
      const params = new URLSearchParams();
      if (status.value) params.set('status', status.value);
      if (priority.value) params.set('priority', priority.value);
      if (q.value.trim()) params.set('q', q.value.trim());
      const rows = await api(`/api/admin/tickets?${params}`);
      resultsWrap.replaceChildren(table(
        [['ticket_id', 'ID'], ['created_at', 'Created'], ['customer_name', 'Name'], ['customer_email', 'Email'], ['category', 'Category'], ['priority', 'Priority'], ['status', 'Status'], ['description', 'Description']],
        rows,
      ));
    }

    [status, priority].forEach((el) => el.addEventListener('change', load));
    let debounce;
    q.addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(load, 250); });

    await load();
    return h('div', {}, h('div', { class: 'admin-filters' }, status, priority, q), resultsWrap);
  }

  // ---------- feedback ----------
  async function feedbackView() {
    const rating = h('select', {}, ['', 'yes', 'no'].map((v) => h('option', { value: v, text: v || 'Any rating' })));
    const intent = h('input', { type: 'search', placeholder: 'Filter by intent…' });
    const resultsWrap = h('div');

    async function load() {
      const params = new URLSearchParams();
      if (rating.value) params.set('rating', rating.value);
      if (intent.value.trim()) params.set('intent', intent.value.trim());
      const rows = await api(`/api/admin/feedback?${params}`);
      resultsWrap.replaceChildren(table(
        [['timestamp', 'When'], ['rating', 'Rating'], ['intent', 'Intent'], ['question', 'Question'], ['reply', 'Reply']],
        rows,
      ));
    }

    rating.addEventListener('change', load);
    let debounce;
    intent.addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(load, 250); });

    await load();
    return h('div', {}, h('div', { class: 'admin-filters' }, rating, intent), resultsWrap);
  }

  // ---------- analytics ----------
  function statCard(title, rows) {
    return h('div', { class: 'stat-card' }, h('h3', { text: title }),
      h('div', { class: 'stat-rows' }, Object.entries(rows).length
        ? Object.entries(rows).map(([k, v]) => h('div', {}, h('span', { text: k || '(none)' }), h('span', { text: String(v) })))
        : h('div', {}, h('span', { text: 'No data yet' }))));
  }

  function trendChart(trend) {
    const width = 640, height = 140, barGap = 2;
    const max = Math.max(1, ...trend.map((d) => d.tickets + d.feedback));
    const barWidth = Math.max(2, width / trend.length - barGap);
    const svg = h('svg', { viewBox: `0 0 ${width} ${height}`, width: '100%', height, role: 'img', 'aria-label': 'Tickets and feedback per day' });
    trend.forEach((d, i) => {
      const x = i * (barWidth + barGap);
      const total = d.tickets + d.feedback;
      const barHeight = (total / max) * (height - 20);
      svg.append(h('rect', { x, y: height - barHeight, width: barWidth, height: barHeight, fill: 'var(--forest-600)' }));
      if (i === trend.length - 1 || i === 0) {
        const label = h('text', { x, y: height - 4, fill: 'var(--brown-500)', 'font-size': '9' });
        label.textContent = d.date.slice(5);
        svg.append(label);
      }
    });
    return svg;
  }

  async function analyticsView() {
    const data = await api('/api/admin/analytics');
    state.analytics = data;
    return h(
      'div', {},
      h('div', { class: 'admin-stats' },
        statCard('Tickets by status', data.tickets.by_status),
        statCard('Tickets by priority', data.tickets.by_priority),
        statCard('Tickets by category', data.tickets.by_category),
        h('div', { class: 'stat-card' }, h('h3', { text: 'Feedback' }),
          h('div', { class: 'stat-big', text: `${Math.round(data.feedback.yes_rate * 100)}%` }),
          h('div', { class: 'stat-rows' },
            h('div', {}, h('span', { text: '👍 Yes' }), h('span', { text: String(data.feedback.yes) })),
            h('div', {}, h('span', { text: '👎 No' }), h('span', { text: String(data.feedback.no) }))))),
      h('div', { class: 'admin-trend' }, h('h3', { text: 'Tickets + feedback, last 30 days' }), trendChart(data.trend)),
    );
  }

  // ---------- start ----------
  async function init() {
    try {
      await api('/api/admin/me');
      showDashboard();
    } catch {
      showLogin();
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
