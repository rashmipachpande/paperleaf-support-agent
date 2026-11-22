/* Paige – the PaperLeaf Books chat widget.
 * Self-contained: add <script src="/static/chat.js" defer></script> to any page and the chat appears.
 * Safe by design: the DOM is built with createElement/textContent only (no innerHTML), so a reply can never inject markup. */
(() => {
  'use strict';

  const KEY_SESSION = 'paige.session.v1';
  const KEY_CHAT = 'paige.chat.v1';
  const MAX_SAVED = 60;
  const YES = /^(yes|yeah|yep|yup|sure|ok|okay|please|please do|yes please|do it|go ahead|create it|create a ticket)\W*$/i;

  const state = { config: null, messages: [], busy: false, open: false, sessionId: '' };
  const nodes = new Map();
  const ui = {};

  // ---------- small helpers ----------
  const store = {
    get: (area, key) => { try { return window[area].getItem(key); } catch { return null; } },
    set: (area, key, value) => { try { window[area].setItem(key, value); } catch { /* storage unavailable: chat still works */ } },
  };
  const uid = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);

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

  /** **bold**, *italic* and line breaks, built as DOM nodes (never HTML). */
  function renderText(text) {
    const frag = document.createDocumentFragment();
    String(text).split('\n').forEach((line, i) => {
      if (i) frag.append(h('br'));
      line.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/).forEach((part) => {
        if (!part) return;
        if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) frag.append(h('strong', { text: part.slice(2, -2) }));
        else if (part.length > 2 && part.startsWith('*') && part.endsWith('*')) frag.append(h('em', { text: part.slice(1, -1) }));
        else frag.append(part);
      });
    });
    return frag;
  }

  const clock = (ts) => new Date(ts).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

  async function api(path, body) {
    let res;
    try {
      res = await fetch(path, body
        ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
        : { method: 'GET' });
    } catch {
      throw { network: true, message: "I couldn't reach the PaperLeaf server. Please check your connection and try again." };
    }
    let data = null;
    try { data = await res.json(); } catch { /* not JSON */ }
    if (!res.ok) {
      const detail = data && data.detail;
      const message = typeof detail === 'string' ? detail
        : Array.isArray(detail) ? 'Please check what you typed and try again.'
        : 'Something went wrong on our side. Please try again in a moment.';
      throw { status: res.status, message };
    }
    return data;
  }

  // ---------- conversation state ----------
  function loadSaved() {
    try {
      const saved = JSON.parse(store.get('sessionStorage', KEY_CHAT) || '[]');
      if (Array.isArray(saved)) {
        return saved.filter((m) => m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string' && m.kind !== 'error');
      }
    } catch { /* corrupted: start fresh */ }
    return [];
  }
  const save = () => store.set('sessionStorage', KEY_CHAT, JSON.stringify(state.messages.filter((m) => m.kind !== 'error').slice(-MAX_SAVED)));

  function pendingOffer() {
    return [...state.messages].reverse().find((m) => m.offer && !m.offerDone) || null;
  }

  function addMessage(partial) {
    const msg = { id: uid(), ts: Date.now(), ...partial };
    state.messages.push(msg);
    save();
    upsert(msg);
    for (const m of state.messages) if (m.offer && m !== msg) upsert(m); // an older ticket form disappears when a newer one appears
    scrollBottom();
    return msg;
  }

  function removeMessage(id) {
    state.messages = state.messages.filter((m) => m.id !== id);
    const node = nodes.get(id);
    if (node) node.remove();
    nodes.delete(id);
    save();
  }

  function historyFor(userMsg) {
    const before = state.messages.slice(0, state.messages.indexOf(userMsg));
    return before
      .filter((m) => !['welcome', 'error', 'ticket'].includes(m.kind))
      .slice(-12)
      .map((m) => ({ role: m.role, content: m.content.slice(0, 2000), intent: m.intent || null }));
  }

  // ---------- rendering ----------
  function upsert(msg) {
    const node = buildMessage(msg);
    const old = nodes.get(msg.id);
    if (old) old.replaceWith(node);
    else if (ui.typing && ui.typing.parentNode) ui.log.insertBefore(node, ui.typing);
    else ui.log.append(node);
    nodes.set(msg.id, node);
  }

  function buildMessage(msg) {
    const wrap = h('div', { class: `msg msg-${msg.role}${msg.kind === 'error' ? ' msg-error' : ''}` });
    const bubble = h('div', { class: `bubble${msg.kind === 'ticket' ? ' ticket-ok' : ''}` }, renderText(msg.content));
    if (msg.kind === 'ticket' && msg.ticketId) {
      bubble.append(h('br'), h('button', { class: 'linkish', type: 'button', onclick: (e) => copyId(msg.ticketId, e.currentTarget) }, 'Copy ticket ID'));
    }
    wrap.append(bubble);

    if (msg.notice) wrap.append(h('span', { class: 'notice', text: `ℹ️ ${msg.notice}` }));
    if (msg.kind === 'error' && msg.retry) {
      wrap.append(h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => retry(msg) }, 'Try again'));
    }
    const pending = pendingOffer();
    if (msg.offer && !msg.offerDone && pending && pending.id === msg.id) wrap.append(buildTicketForm(msg));
    if (msg.role === 'assistant' && !msg.kind) wrap.append(buildFeedback(msg));
    if (msg.kind !== 'error') wrap.append(h('span', { class: 'meta', text: clock(msg.ts) }));
    return wrap;
  }

  function buildTicketForm(msg) {
    const cfg = state.config || { ticket_categories: [msg.offer.category] };
    const categories = cfg.ticket_categories.includes(msg.offer.category) ? cfg.ticket_categories : [msg.offer.category, ...cfg.ticket_categories];
    const name = h('input', { type: 'text', maxlength: 80, autocomplete: 'name' });
    const email = h('input', { type: 'email', maxlength: 120, autocomplete: 'email' });
    const category = h('select', {}, categories.map((c) => h('option', { value: c, text: c })));
    category.value = msg.offer.category;
    const desc = h('textarea', { maxlength: 1500, rows: 3 });
    desc.value = msg.offer.description || '';
    const error = h('p', { class: 'field-error', role: 'alert', hidden: true });
    const submit = h('button', { class: 'btn btn-primary', type: 'submit' }, '🎫 Create ticket');

    const form = h(
      'form',
      {
        class: 'ticket-card', novalidate: true, 'data-ticket-form': msg.id,
        onsubmit: async (e) => {
          e.preventDefault();
          error.hidden = true;
          if (desc.value.trim().length < 5) { error.textContent = 'Please describe the issue in a few words.'; error.hidden = false; return; }
          submit.disabled = true;
          submit.textContent = 'Creating…';
          try {
            const res = await api('/api/tickets', {
              session_id: state.sessionId, category: category.value, priority: msg.offer.priority, reason: msg.offer.reason || '',
              description: desc.value.trim(), name: name.value.trim(), email: email.value.trim(),
            });
            msg.offerDone = true;
            save();
            upsert(msg);
            addMessage({ role: 'assistant', kind: 'ticket', content: res.text, ticketId: res.ticket_id, intent: 'ticket' });
          } catch (err) {
            error.textContent = err.message;
            error.hidden = false;
            submit.disabled = false;
            submit.textContent = '🎫 Create ticket';
          }
        },
      },
      h('h4', { text: '🎫 Support ticket' }),
      h('label', {}, 'Your name (optional)', name),
      h('label', {}, 'Email (optional)', email),
      h('label', {}, 'Issue category', category),
      h('label', {}, 'What happened?', desc),
      error,
      h('div', { class: 'ticket-actions' }, submit, h('button', {
        class: 'btn btn-ghost', type: 'button',
        onclick: () => {
          msg.offerDone = true;
          save();
          upsert(msg);
          addMessage({ role: 'assistant', content: "No problem at all! 😊 I'm here if you change your mind or have another question.", intent: 'smalltalk' });
        },
      }, 'No thanks')),
    );
    return form;
  }

  function buildFeedback(msg) {
    if (msg.feedback === 'yes' || msg.feedback === 'no') return h('div', { class: 'feedback', text: 'Thanks for your feedback! 💚' });
    const row = h('div', { class: 'feedback' }, h('span', { text: msg.feedback === 'error' ? "Couldn't save that. Try again?" : 'Was this helpful?' }));
    [['yes', '👍 Yes'], ['no', '👎 No']].forEach(([rating, label]) =>
      row.append(h('button', { class: 'fb-btn', type: 'button', 'aria-label': `${rating === 'yes' ? 'Helpful' : 'Not helpful'}`, onclick: () => rate(msg, rating) }, label)));
    return row;
  }

  async function rate(msg, rating) {
    const idx = state.messages.indexOf(msg);
    const question = [...state.messages.slice(0, idx)].reverse().find((m) => m.role === 'user');
    try {
      await api('/api/feedback', { rating, question: question ? question.content : '', reply: msg.content, intent: msg.intent || '' });
      msg.feedback = rating;
    } catch {
      msg.feedback = 'error';
    }
    save();
    upsert(msg);
  }

  async function copyId(id, button) {
    try {
      await navigator.clipboard.writeText(id);
      button.textContent = 'Copied ✓';
    } catch {
      button.textContent = id; // clipboard blocked: show it so it can be selected
    }
  }

  function scrollBottom() { requestAnimationFrame(() => { ui.log.scrollTop = ui.log.scrollHeight; }); }

  function setBusy(busy) {
    state.busy = busy;
    ui.send.disabled = busy;
    ui.quick.querySelectorAll('button').forEach((b) => { b.disabled = busy; });
    if (busy) { ui.log.append(ui.typing); scrollBottom(); } else ui.typing.remove();
  }

  // ---------- sending ----------
  async function send(text, retryUserMsg) {
    text = (text || '').trim();
    if (!text || state.busy) return;
    if (!state.open) setOpen(true);

    const waiting = pendingOffer();
    if (!retryUserMsg && waiting && YES.test(text)) {
      addMessage({ role: 'user', content: text, intent: 'smalltalk' });
      addMessage({ role: 'assistant', content: 'Sure! Please check the details in the ticket form above and press **Create ticket**.', intent: 'smalltalk' });
      const form = ui.log.querySelector(`[data-ticket-form="${waiting.id}"]`);
      if (form) form.scrollIntoView({ block: 'center' });
      return;
    }

    const userMsg = retryUserMsg || addMessage({ role: 'user', content: text });
    setBusy(true);
    try {
      const data = await api('/api/chat', { message: text, history: historyFor(userMsg) });
      userMsg.content = data.user_text || userMsg.content; // card numbers / OTPs are masked by the server
      userMsg.intent = data.intent;
      upsert(userMsg);
      addMessage({ role: 'assistant', content: data.text, intent: data.intent, offer: data.offer || undefined, notice: data.notice || undefined });
    } catch (err) {
      addMessage({ role: 'assistant', kind: 'error', content: err.message, retry: { text, userMsgId: userMsg.id } });
    } finally {
      setBusy(false);
    }
  }

  function retry(errorMsg) {
    removeMessage(errorMsg.id);
    if (errorMsg.retry.config) { ensureWelcome(); return; } // the first connection failed: try setting up again
    const userMsg = state.messages.find((m) => m.id === errorMsg.retry.userMsgId);
    if (userMsg) send(errorMsg.retry.text, userMsg);
  }

  // ---------- panel ----------
  async function loadConfig() {
    if (state.config) return true;
    try {
      state.config = await api('/api/config');
      buildQuickReplies();
      renderTopics();
      return true;
    } catch {
      return false;
    }
  }

  async function ensureWelcome() {
    if (state.messages.length) return;
    if (await loadConfig()) addMessage({ role: 'assistant', kind: 'welcome', content: state.config.greeting });
    else addMessage({ role: 'assistant', kind: 'error', content: "I couldn't connect just now. Please check your connection and try again.", retry: { text: '', userMsgId: null, config: true } });
  }

  function setOpen(open, focus = true) {
    state.open = open;
    ui.panel.hidden = !open;
    ui.launcher.setAttribute('aria-expanded', String(open));
    if (open) {
      ensureWelcome().then(scrollBottom);
      if (focus) ui.input.focus();
    } else if (focus) {
      ui.launcher.focus();
    }
  }

  function clearChat() {
    if (!window.confirm('Clear this conversation?')) return;
    state.messages = [];
    nodes.clear();
    ui.log.replaceChildren();
    save();
    ensureWelcome();
  }

  function buildQuickReplies() {
    ui.quick.replaceChildren(...state.config.quick_replies.map((q) => h('button', { class: 'chip', type: 'button', onclick: () => send(q.message) }, q.label)));
  }

  function renderTopics() {
    document.querySelectorAll('[data-paige-topics]').forEach((grid) => {
      grid.replaceChildren(...state.config.topics.map((t) =>
        h('button', { class: 'topic', type: 'button', onclick: () => send(t.question) },
          h('span', { class: 'topic-icon', 'aria-hidden': 'true', text: t.icon }),
          h('span', { class: 'topic-title', text: t.title }),
          h('span', { class: 'topic-ask', text: 'Ask Paige →' }))));
    });
  }

  function build() {
    ui.launcher = h('button', { class: 'paige-launcher', type: 'button', 'aria-expanded': 'false', 'aria-controls': 'paige-panel', 'aria-label': 'Chat with Paige', onclick: () => setOpen(true) },
      h('img', { src: '/assets/logo.png', alt: '', width: 40, height: 40 }), h('span', { text: 'Ask Paige' }));

    ui.log = h('div', { class: 'paige-log', role: 'log', 'aria-live': 'polite', 'aria-label': 'Conversation with Paige', tabindex: '0' });
    ui.typing = h('div', { class: 'typing', role: 'status' }, h('span'), h('span'), h('span'), h('span', { class: 'sr-only', text: 'Paige is typing' }));
    ui.quick = h('div', { class: 'paige-quick', 'aria-label': 'Quick questions' });
    ui.input = h('textarea', { rows: 1, maxlength: 1000, placeholder: 'Ask Paige a question…', 'aria-label': 'Your message', enterkeyhint: 'send' });
    ui.send = h('button', { class: 'paige-send', type: 'submit', 'aria-label': 'Send message' }, '➤');
    ui.count = h('span', { class: 'paige-count' });

    const form = h('form', { class: 'paige-form', onsubmit: (e) => { e.preventDefault(); const t = ui.input.value; ui.input.value = ''; autosize(); send(t); } }, ui.input, ui.send);
    ui.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
    });
    ui.input.addEventListener('input', autosize);

    ui.panel = h('section', { class: 'paige-panel', id: 'paige-panel', role: 'dialog', 'aria-label': 'Chat with Paige', hidden: true },
      h('header', { class: 'paige-head' },
        h('img', { src: '/assets/logo.png', alt: '', width: 38, height: 38 }),
        h('div', { class: 'paige-title' }, h('strong', { text: 'Paige' }), h('span', { text: 'PaperLeaf Books assistant' })),
        h('button', { class: 'paige-icon-btn', type: 'button', 'aria-label': 'Clear conversation', title: 'Clear conversation', onclick: clearChat }, '🗑'),
        h('button', { class: 'paige-icon-btn', type: 'button', 'aria-label': 'Minimize chat', title: 'Minimize', onclick: () => setOpen(false) }, '–')),
      ui.log, ui.quick, form,
      h('p', { class: 'paige-foot' }, h('span', { text: 'Paige is an AI assistant. Never share card numbers, OTPs or passwords.' }), ui.count));
    ui.panel.addEventListener('keydown', (e) => { if (e.key === 'Escape') setOpen(false); });

    document.body.append(ui.launcher, ui.panel);
    document.querySelectorAll('[data-paige-open]').forEach((b) => b.addEventListener('click', () => setOpen(true)));
  }

  function autosize() {
    ui.input.style.height = 'auto';
    ui.input.style.height = `${Math.min(ui.input.scrollHeight, 110)}px`;
    ui.input.style.overflowY = ui.input.scrollHeight > 110 ? 'auto' : 'hidden';
    const left = 1000 - ui.input.value.length;
    ui.count.textContent = left < 200 ? `${left} left` : '';
  }

  // ---------- start ----------
  function init() {
    state.sessionId = store.get('localStorage', KEY_SESSION) || uid().replace(/[^A-Za-z0-9_-]/g, '');
    store.set('localStorage', KEY_SESSION, state.sessionId);
    state.messages = loadSaved();
    build();
    state.messages.forEach(upsert);
    const year = document.getElementById('year');
    if (year) year.textContent = new Date().getFullYear();
    loadConfig();
    window.PaigeChat = { open: () => setOpen(true), ask: (q) => send(q) };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
