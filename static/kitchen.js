/* Kitchen dashboard: live order list, per-person and global game pause, look-and-feel helpers. */
(function () {
  const B = document.body;
  const BASE = B.dataset.base || '';
  const CSRF = B.dataset.csrf || '';
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  async function post(path, data) {
    const r = await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF': CSRF },
      body: JSON.stringify(data || {}),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  const clock = (s) => new Date(s * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

  const cols = { pending: $('#col-pending'), ready: $('#col-ready'), done: $('#col-done') };
  const pauseBtn = $('#pause-all');
  const gamesState = $('#games-state');
  let lastJson = '';
  let known = null;
  let unseen = 0;
  const baseTitle = document.title;

  async function act(id, action, body) {
    try { await post('/kitchen/api/orders/' + id + '/' + action, body); } catch (e) { alert('That did not go through. Try again.'); }
    load();
  }

  function ticket(o, fresh) {
    const t = el('article', 'ticket' + (fresh ? ' fresh' : '') + (o.gamesPaused ? ' is-paused' : ''));
    const head = el('div', 't-head');
    head.append(el('span', 't-name', o.name), el('span', null, '#' + o.id + ' · ' + clock(o.createdAt)));
    t.append(head);

    const ul = el('ul');
    o.items.forEach((it) => {
      const li = el('li');
      li.append(el('b', null, it.qty + ' ×'), el('span', null, it.name));
      if (it.selfServe) li.append(el('em', 'selftag', 'serving themselves'));
      ul.append(li);
    });
    t.append(ul);
    if (o.note) t.append(el('p', 't-note', 'Note: ' + o.note));

    t.append(el('p', 't-meta', o.email));
    if (o.notifyStatus && o.notifyStatus !== 'sent' && o.notifyStatus !== 'sending') {
      t.append(el('p', 't-meta bad', 'The email to you about this order failed. ' + o.notifyStatus.replace(/^failed: /, '')));
    }
    if (o.status !== 'pending') {
      const s = o.readyEmailStatus;
      if (s === 'sent') t.append(el('p', 't-meta', 'Ready email sent.'));
      else if (s === 'sending') t.append(el('p', 't-meta', 'Sending ready email...'));
      else if (s) t.append(el('p', 't-meta bad', 'Ready email failed: ' + s.replace(/^failed: /, '')));
    }
    if (o.gamesPaused) t.append(el('p', 't-meta pz', 'Games paused for ' + o.name + '.'));

    const btns = el('div', 't-btns');
    if (o.status === 'pending') {
      const b = el('button', 'btn primary', 'Ready');
      b.type = 'button'; b.onclick = () => act(o.id, 'ready');
      btns.append(b);
    }
    if (o.status === 'ready') {
      const b = el('button', 'btn primary', 'Picked up');
      b.type = 'button'; b.onclick = () => act(o.id, 'done');
      btns.append(b);
      if (o.readyEmailStatus && o.readyEmailStatus !== 'sent' && o.readyEmailStatus !== 'sending') {
        const r = el('button', 'btn', 'Resend email');
        r.type = 'button'; r.onclick = () => act(o.id, 'resend');
        btns.append(r);
      }
    }
    if (o.status !== 'done') {
      const p = el('button', 'btn', o.gamesPaused ? 'Resume their games' : 'Pause their games');
      p.type = 'button'; p.onclick = () => act(o.id, 'games', { paused: !o.gamesPaused });
      btns.append(p);
    }
    if (btns.children.length) t.append(btns);
    return t;
  }

  function render(data) {
    const ids = data.orders.map((o) => o.id);
    const fresh = new Set();
    if (known) {
      ids.forEach((id) => { if (!known.has(id)) fresh.add(id); });
      const pendingFresh = data.orders.filter((o) => fresh.has(o.id) && o.status === 'pending').length;
      if (pendingFresh) {
        unseen += pendingFresh;
        document.title = '(' + unseen + ') New order | ' + baseTitle;
      }
    }
    known = new Set(ids);

    Object.values(cols).forEach((c) => { c.textContent = ''; });
    // Oldest waiting order first; done list newest first.
    data.orders.filter((o) => o.status === 'pending').forEach((o) => cols.pending.append(ticket(o, fresh.has(o.id))));
    data.orders.filter((o) => o.status === 'ready').forEach((o) => cols.ready.append(ticket(o, false)));
    data.orders.filter((o) => o.status === 'done').reverse().forEach((o) => cols.done.append(ticket(o, false)));

    const waiting = data.orders.filter((o) => o.status === 'pending').length;
    $('#count').textContent = waiting ? '(' + waiting + ' waiting)' : '';
    $('#orders-empty').hidden = data.orders.length > 0;

    pauseBtn.textContent = data.pausedAll ? "Resume everyone's games" : "Pause everyone's games";
    pauseBtn.dataset.paused = data.pausedAll ? '1' : '';
    gamesState.textContent = data.pausedAll ? 'Games are paused for everyone.' : 'Games are running.';
  }

  async function load() {
    try {
      const r = await fetch(BASE + '/kitchen/api/orders', { cache: 'no-store' });
      if (r.status === 401) { location.reload(); return; }
      const data = await r.json();
      const js = JSON.stringify(data);
      if (js === lastJson) return;
      lastJson = js;
      render(data);
    } catch (e) { /* try again next tick */ }
  }

  pauseBtn.addEventListener('click', async () => {
    const next = !pauseBtn.dataset.paused;
    try { await post('/kitchen/api/games-all', { paused: next }); } catch (e) { alert('That did not go through. Try again.'); }
    load();
  });

  const clearUnseen = () => { unseen = 0; document.title = baseTitle; };
  window.addEventListener('focus', clearUnseen);
  document.addEventListener('click', clearUnseen);

  /* ---- Look form: emoji buttons and colour presets ---- */
  const emojiInput = $('#emoji-input');
  $$('.emoji-pick button').forEach((b) => b.addEventListener('click', () => { emojiInput.value = b.dataset.emoji; emojiInput.focus(); }));
  $$('.preset').forEach((b) => b.addEventListener('click', () => {
    $('input[name=color_bg]').value = b.dataset.bg;
    $('input[name=color_ink]').value = b.dataset.ink;
    $('input[name=color_accent]').value = b.dataset.accent;
  }));

  load();
  setInterval(load, 4000);
})();
