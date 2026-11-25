/* Shared bits: steppers, the announcement bar, live site name/colours, leaderboards. */
(function () {
  const B = document.body;
  const BASE = B.dataset.base || '';
  const TOKEN = B.dataset.token || '';
  const POLL = parseInt(B.dataset.poll || '0', 10);
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  const ls = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode, fine */ } },
  };

  const Lazy = window.Lazy = {
    base: BASE,
    token: TOKEN,
    handlers: [],
    onState(fn) { this.handlers.push(fn); },
  };

  /* ---- order steppers ---- */
  function syncPicked(input) {
    const li = input.closest('.line');
    if (li) li.classList.toggle('picked', (parseInt(input.value, 10) || 0) > 0);
  }
  document.addEventListener('click', (e) => {
    const b = e.target.closest('.stepper button');
    if (!b) return;
    const input = $('input', b.parentElement);
    const max = parseInt(input.max || '99', 10);
    const next = (parseInt(input.value, 10) || 0) + parseInt(b.dataset.d, 10);
    input.value = Math.max(0, Math.min(max, next));
    syncPicked(input);
  });
  document.addEventListener('input', (e) => {
    if (e.target.matches('.stepper input')) syncPicked(e.target);
  });

  /* ---- keep each menu item on one line by shrinking long names ---- */
  function fitNames() {
    $$('.lines:not(.plain) .line').forEach((li) => {
      const name = $('label:not(.self)', li);
      if (!name) return;
      name.style.fontSize = '';
      let fs = parseFloat(getComputedStyle(name).fontSize);
      // The name is allowed to shrink in the row; scrollWidth is what it wants, clientWidth what it got.
      while (name.scrollWidth > name.clientWidth + 0.5 && fs > 12) { fs -= 0.5; name.style.fontSize = fs + 'px'; }
    });
  }
  if ($('.lines')) {
    fitNames();
    window.addEventListener('resize', fitNames);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitNames);
  }

  /* ---- announcement bar ---- */
  function renderAnn(a) {
    const box = $('#ann');
    if (!box) return;
    if (!a || !a.text || String(a.id) === ls.get('lazy-ann-closed')) { box.hidden = true; return; }
    $('#ann-text').textContent = a.text;
    box.dataset.id = a.id;
    box.hidden = false;
  }
  const annX = $('#ann-x');
  if (annX) annX.addEventListener('click', () => {
    ls.set('lazy-ann-closed', $('#ann').dataset.id);
    $('#ann').hidden = true;
  });
  // The server renders the bar already, but honour an earlier dismissal straight away.
  if ($('#ann') && !$('#ann').hidden && $('#ann').dataset.id === ls.get('lazy-ann-closed')) $('#ann').hidden = true;

  /* ---- live name, emoji, colours ---- */
  function applySite(s, theme) {
    if (s) {
      $$('[data-site-name]').forEach((n) => { n.textContent = s.name; });
      $$('[data-site-emoji]').forEach((n) => { n.textContent = s.emoji; });
      B.dataset.emoji = s.emoji;
      B.dataset.site = s.name;
      const pt = B.dataset.titleOverride || B.dataset.pagetitle;
      document.title = pt ? pt + ' | ' + s.name : s.name;
      const fav = $('#favicon');
      const want = BASE + '/favicon.svg?v=' + encodeURIComponent(s.emoji);
      if (fav && fav.getAttribute('href') !== want) fav.setAttribute('href', want);
    }
    if (theme) for (const k in theme) document.documentElement.style.setProperty(k, theme[k]);
  }

  /* ---- polling ---- */
  async function refresh() {
    try {
      const r = await fetch(BASE + '/api/state' + (TOKEN ? '?t=' + encodeURIComponent(TOKEN) : ''), { cache: 'no-store' });
      if (!r.ok) return;
      const st = await r.json();
      Lazy.state = st;
      applySite(st.site, st.theme);
      renderAnn(st.announcement);
      Lazy.handlers.forEach((fn) => { try { fn(st); } catch (e) { console.error(e); } });
    } catch (e) { /* offline: try again next tick */ }
  }
  Lazy.refresh = refresh;
  if (POLL) {
    refresh();
    setInterval(refresh, POLL);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  }

  /* ---- leaderboards ---- */
  Lazy.renderBoards = function (boards) {
    const me = (B.dataset.me || '').toLowerCase();
    $$('.board').forEach((ol) => {
      const rows = boards[ol.dataset.game] || [];
      ol.textContent = '';
      if (!rows.length) {
        const li = document.createElement('li');
        li.className = 'empty';
        li.textContent = 'Nobody yet. Be the first.';
        ol.appendChild(li);
        return;
      }
      rows.forEach((r) => {
        const li = document.createElement('li');
        if (me && r.name.toLowerCase() === me) li.className = 'me';
        const nm = document.createElement('span'); nm.className = 'nm'; nm.textContent = r.name;
        const gap = document.createElement('span'); gap.className = 'leader';
        const sc = document.createElement('b'); sc.textContent = r.score;
        li.append(nm, gap, sc);
        ol.appendChild(li);
      });
    });
  };
  Lazy.loadBoards = async function () {
    try {
      const r = await fetch(BASE + '/api/scores', { cache: 'no-store' });
      if (r.ok) Lazy.renderBoards(await r.json());
    } catch (e) { /* ignore */ }
  };
  if ($('.board')) {
    Lazy.loadBoards();
    setInterval(Lazy.loadBoards, 20000);
  }
})();
