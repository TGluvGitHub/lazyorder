/* Snake and Flappy for the waiting page. No libraries, no assets: it all draws with the site's colours. */
(function () {
  const stage = document.getElementById('stage');
  if (!stage || !window.Lazy) return;

  const B = document.body;
  const BASE = B.dataset.base || '';
  const TOKEN = B.dataset.token || '';
  const $ = (id) => document.getElementById(id);
  const cv = { snake: $('cv-snake'), flappy: $('cv-flappy') };
  const ov = $('ov'), ovMsg = $('ov-msg'), ovBtn = $('ov-btn');
  const dpad = $('dpad'), hint = $('hint');
  const tabs = Array.from(document.querySelectorAll('.tabs button'));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const TYPE = '"American Typewriter","Courier New",Courier,monospace';
  const EMOJI_FONT = '"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif';
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  let pal = {};
  function readPalette() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n) => cs.getPropertyValue(n).trim();
    pal = { paper: v('--paper'), ink: v('--ink'), accent: v('--accent'), line: v('--line') };
  }
  readPalette();

  function prep(canvas, w, h) {
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.aspectRatio = w + ' / ' + h;
    const c = canvas.getContext('2d');
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    return c;
  }

  function hud(c, text) {
    c.font = '700 15px ' + TYPE;
    const w = c.measureText(text).width + 14;
    c.fillStyle = pal.paper;
    c.fillRect(0, 0, w, 26);
    c.fillStyle = pal.ink;
    c.textAlign = 'left';
    c.textBaseline = 'middle';
    c.fillText(text, 7, 14);
  }

  /* ------------------------------------------------------------------ */
  /* Snake                                                               */
  /* ------------------------------------------------------------------ */
  const Snake = {
    name: 'snake', W: 360, H: 360, N: 18,
    ctx: prep(cv.snake, 360, 360),
    intro: "Snake. Eat, grow, and don't hit the walls or yourself.",
    hint: 'Arrow keys or WASD. On a phone, swipe on the board or use the buttons.',
    reset() {
      this.body = [{ x: 8, y: 9 }, { x: 7, y: 9 }, { x: 6, y: 9 }];
      this.dir = { x: 1, y: 0 };
      this.queue = [];
      this.speed = 135;
      this.acc = 0;
      this.note = '';
      this.state = 'idle';
      this.placeFood();
    },
    get score() { return this.body.length; },
    deadMsg() { return 'You got ' + this.score + ' long.'; },
    placeFood() {
      const taken = new Set(this.body.map((p) => p.x + ',' + p.y));
      const free = [];
      for (let y = 0; y < this.N; y++) for (let x = 0; x < this.N; x++) if (!taken.has(x + ',' + y)) free.push({ x, y });
      this.food = free.length ? free[Math.floor(Math.random() * free.length)] : null;
    },
    go() { if (this.state === 'dead') this.reset(); this.state = 'running'; },
    setDir(x, y) {
      const last = this.queue.length ? this.queue[this.queue.length - 1] : this.dir;
      if ((x === -last.x && y === -last.y) || (x === last.x && y === last.y)) return;
      if (this.queue.length < 2) this.queue.push({ x, y });
    },
    update(dt) {
      this.acc += dt;
      while (this.acc >= this.speed && this.state === 'running') {
        this.acc -= this.speed;
        this.tick();
      }
    },
    tick() {
      if (this.queue.length) this.dir = this.queue.shift();
      const h = this.body[0];
      const nx = h.x + this.dir.x, ny = h.y + this.dir.y;
      if (nx < 0 || ny < 0 || nx >= this.N || ny >= this.N) return this.die();
      const eat = !!this.food && nx === this.food.x && ny === this.food.y;
      const solid = eat ? this.body.length : this.body.length - 1; // the tail steps out of the way when we don't grow
      for (let i = 0; i < solid; i++) if (this.body[i].x === nx && this.body[i].y === ny) return this.die();
      this.body.unshift({ x: nx, y: ny });
      if (eat) {
        this.speed = Math.max(70, this.speed - 3);
        this.placeFood();
        if (!this.food) { this.note = 'You filled the whole board!'; return this.die(); }
      } else {
        this.body.pop();
      }
    },
    die() { this.state = 'dead'; finish(this); },
    draw() {
      const c = this.ctx, s = this.W / this.N;
      c.fillStyle = pal.paper;
      c.fillRect(0, 0, this.W, this.H);
      c.fillStyle = pal.line;
      for (let y = 0; y < this.N; y++) for (let x = 0; x < this.N; x++) c.fillRect(x * s + s / 2 - 1, y * s + s / 2 - 1, 2, 2);
      if (this.food) {
        c.beginPath();
        c.arc(this.food.x * s + s / 2, this.food.y * s + s / 2, s * 0.34, 0, Math.PI * 2);
        c.fillStyle = pal.accent; c.fill();
        c.lineWidth = 2; c.strokeStyle = pal.ink; c.stroke();
      }
      this.body.forEach((p, i) => {
        c.fillStyle = i === 0 ? pal.accent : pal.ink;
        c.fillRect(p.x * s + 2, p.y * s + 2, s - 4, s - 4);
        if (i === 0) { c.lineWidth = 2; c.strokeStyle = pal.ink; c.strokeRect(p.x * s + 2, p.y * s + 2, s - 4, s - 4); }
      });
      hud(c, 'LENGTH ' + this.score);
    },
  };

  /* ------------------------------------------------------------------ */
  /* Flappy (the bird is whatever emoji the kitchen picked)              */
  /* ------------------------------------------------------------------ */
  const Flappy = {
    name: 'flappy', W: 360, H: 480, GROUND: 40,
    GAP: 140, PW: 56, SPEED: 2.4, SPACING: 200, GRAV: 0.42, FLAP: -6.6, R: 11, BX: 90,
    ctx: prep(cv.flappy, 360, 480),
    intro: 'Flappy. Tap, click or hit space to flap. Slip through the gaps.',
    hint: 'Space, click or tap to flap.',
    reset() {
      this.y = 210; this.vy = 0; this.score = 0; this.dist = 0; this.acc = 0;
      this.lastGap = 240; this.pipes = []; this.note = ''; this.state = 'idle';
      this.spawn(this.W + 80);
    },
    deadMsg() { return this.score === 0 ? 'Ouch. Zero.' : 'You cleared ' + this.score + (this.score === 1 ? ' gap.' : ' gaps.'); },
    spawn(x) {
      this.lastGap = clamp(this.lastGap + (Math.random() * 2 - 1) * 120, 110, 330);
      this.pipes.push({ x, gap: this.lastGap, scored: false });
    },
    go() { if (this.state === 'dead') this.reset(); this.state = 'running'; this.vy = this.FLAP; },
    flap() { if (this.state === 'running') this.vy = this.FLAP; },
    update(dt) {
      this.acc += dt;
      let n = 0;
      while (this.acc >= 16.667 && n < 5 && this.state === 'running') { this.acc -= 16.667; n++; this.step(); }
      if (n === 5) this.acc = 0;
    },
    step() {
      this.vy += this.GRAV;
      this.y += this.vy;
      if (this.y - this.R < 0) { this.y = this.R; this.vy = 0; }
      this.dist += this.SPEED;
      this.pipes.forEach((p) => { p.x -= this.SPEED; });
      if (this.pipes[this.pipes.length - 1].x < this.W - this.SPACING) this.spawn(this.W + 10);
      if (this.pipes[0].x + this.PW < -10) this.pipes.shift();
      for (const p of this.pipes) {
        if (!p.scored && p.x + this.PW < this.BX - this.R) { p.scored = true; this.score++; }
        const hitsX = this.BX + this.R > p.x - 4 && this.BX - this.R < p.x + this.PW + 4;
        if (hitsX && (this.y - this.R < p.gap - this.GAP / 2 || this.y + this.R > p.gap + this.GAP / 2)) return this.die();
      }
      if (this.y + this.R > this.H - this.GROUND) { this.y = this.H - this.GROUND - this.R; return this.die(); }
    },
    die() { this.state = 'dead'; finish(this); },
    draw(t) {
      const c = this.ctx, H = this.H, G = this.GROUND, CAP = 16;
      c.fillStyle = pal.paper;
      c.fillRect(0, 0, this.W, H);
      for (const p of this.pipes) {
        const top = p.gap - this.GAP / 2, bot = p.gap + this.GAP / 2;
        c.fillStyle = pal.ink;
        c.fillRect(p.x, 0, this.PW, top - CAP);
        c.fillRect(p.x, bot + CAP, this.PW, H - G - bot - CAP);
        c.fillStyle = pal.accent;
        c.fillRect(p.x - 4, top - CAP, this.PW + 8, CAP);
        c.fillRect(p.x - 4, bot, this.PW + 8, CAP);
        c.lineWidth = 2; c.strokeStyle = pal.ink;
        c.strokeRect(p.x - 4, top - CAP, this.PW + 8, CAP);
        c.strokeRect(p.x - 4, bot, this.PW + 8, CAP);
      }
      c.fillStyle = pal.ink;
      c.fillRect(0, H - G, this.W, G);
      c.fillStyle = pal.accent;
      c.fillRect(0, H - G, this.W, 4);
      c.strokeStyle = pal.paper; c.lineWidth = 2; c.globalAlpha = 0.35;
      for (let x = -(this.dist % 24); x < this.W; x += 24) { c.beginPath(); c.moveTo(x, H - G + 8); c.lineTo(x - 10, H); c.stroke(); }
      c.globalAlpha = 1;

      const bob = this.state === 'idle' ? Math.sin(t / 260) * 6 : 0;
      const y = this.y + bob;
      const em = document.body.dataset.emoji || '';
      c.save();
      c.translate(this.BX, y);
      c.rotate(clamp(this.vy * 0.07, -0.5, 1.1));
      if (em) {
        c.font = '28px ' + EMOJI_FONT; c.textAlign = 'center'; c.textBaseline = 'middle';
        c.fillText(em, 0, 2);
      } else {
        c.beginPath(); c.arc(0, 0, this.R, 0, Math.PI * 2); c.fillStyle = pal.accent; c.fill(); c.stroke();
      }
      c.restore();

      c.font = '800 46px ' + TYPE; c.textAlign = 'center'; c.textBaseline = 'alphabetic';
      c.lineWidth = 6; c.strokeStyle = pal.paper; c.strokeText(String(this.score), this.W / 2, 62);
      c.fillStyle = pal.ink; c.fillText(String(this.score), this.W / 2, 62);
    },
  };

  /* ------------------------------------------------------------------ */
  /* Controller                                                          */
  /* ------------------------------------------------------------------ */
  const games = { snake: Snake, flappy: Flappy };
  let current = 'snake';
  let blocked = false;               // the kitchen paused games
  let last = performance.now();
  const G = () => games[current];
  Snake.reset(); Flappy.reset();

  function setOverlay(msg, btn) {
    ovMsg.textContent = msg;
    ovBtn.hidden = !btn;
    if (btn) ovBtn.textContent = btn;
    ov.hidden = false;
  }
  function syncOverlay() {
    const g = G();
    if (blocked) { setOverlay('The kitchen paused the games. Hang tight, they will be back.', null); return; }
    if (g.state === 'running') { ov.hidden = true; return; }
    if (g.state === 'idle') { setOverlay(g.intro, 'Start'); return; }
    if (g.state === 'held') { setOverlay('Paused. Your run is still here.', 'Carry on'); return; }
    setOverlay(g.deadMsg() + (g.note ? ' ' + g.note : ''), 'Go again');
  }
  function begin() {
    if (blocked || G().state === 'running') return;
    G().go();
    syncOverlay();
  }
  function hold() {
    const g = G();
    if (g.state === 'running') { g.state = 'held'; syncOverlay(); }
  }

  async function submit(g, score) {
    if (!TOKEN) return;
    try {
      const r = await fetch(BASE + '/api/score', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ t: TOKEN, game: g.name, score }),
      });
      if (!r.ok) return;
      const j = await r.json();
      if (j.boards) Lazy.renderBoards(j.boards);
      if (j.best) {
        g.note = 'New best for you!';
        if (g.state === 'dead' && games[current] === g) syncOverlay();
      }
    } catch (e) { /* the score just doesn't post */ }
  }
  function finish(g) {
    g.note = g.note || '';
    syncOverlay();
    if (g.score >= (g.name === 'snake' ? 4 : 1)) submit(g, g.score);
  }

  function frame(t) {
    requestAnimationFrame(frame);
    const dt = Math.min(50, t - last);
    last = t;
    const g = G();
    if (!blocked && g.state === 'running') g.update(dt);
    g.draw(t);
  }

  function show(name) {
    if (name === current) return;
    hold();
    current = name;
    cv.snake.hidden = name !== 'snake';
    cv.flappy.hidden = name !== 'flappy';
    tabs.forEach((b) => b.classList.toggle('on', b.dataset.g === name));
    dpad.classList.toggle('on', name === 'snake');
    hint.textContent = G().hint;
    syncOverlay();
  }

  /* ---- input ---- */
  const DIRS = {
    ArrowUp: [0, -1], KeyW: [0, -1], ArrowDown: [0, 1], KeyS: [0, 1],
    ArrowLeft: [-1, 0], KeyA: [-1, 0], ArrowRight: [1, 0], KeyD: [1, 0],
  };
  function inView() {
    const r = stage.getBoundingClientRect();
    return r.bottom > 0 && r.top < window.innerHeight;
  }
  window.addEventListener('keydown', (e) => {
    if (blocked || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.closest && e.target.closest('input, textarea, select')) return;
    const g = G();
    if (e.code === 'Space' || e.code === 'Enter') {
      if (!inView()) return;
      if (g.state !== 'running') { e.preventDefault(); begin(); }
      else if (current === 'flappy' && e.code === 'Space') { e.preventDefault(); if (!e.repeat) g.flap(); }
      return;
    }
    const d = DIRS[e.code];
    if (d && current === 'snake' && g.state === 'running') { e.preventDefault(); g.setDir(d[0], d[1]); }
  });

  let touch = null;
  stage.addEventListener('pointerdown', (e) => {
    if (e.target === ovBtn || blocked) return;
    if (current === 'flappy') {
      if (G().state === 'running') G().flap(); else begin();
      return;
    }
    touch = { x: e.clientX, y: e.clientY, swiped: false };
  });
  stage.addEventListener('pointermove', (e) => {
    if (!touch || current !== 'snake') return;
    const dx = e.clientX - touch.x, dy = e.clientY - touch.y;
    if (Math.max(Math.abs(dx), Math.abs(dy)) < 24) return;
    touch.swiped = true;
    if (G().state === 'running') {
      if (Math.abs(dx) > Math.abs(dy)) Snake.setDir(Math.sign(dx), 0); else Snake.setDir(0, Math.sign(dy));
    }
    touch.x = e.clientX; touch.y = e.clientY;
  });
  const endTouch = () => {
    if (touch && !touch.swiped && !blocked && G().state !== 'running') begin();
    touch = null;
  };
  stage.addEventListener('pointerup', endTouch);
  stage.addEventListener('pointercancel', () => { touch = null; });

  ovBtn.addEventListener('click', begin);
  tabs.forEach((b) => b.addEventListener('click', () => show(b.dataset.g)));
  dpad.addEventListener('pointerdown', (e) => {
    const b = e.target.closest('button');
    if (!b || blocked || current !== 'snake') return;
    e.preventDefault();
    if (Snake.state !== 'running') begin();
    const d = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] }[b.dataset.dir];
    Snake.setDir(d[0], d[1]);
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) hold(); });

  /* ---- what the kitchen says: pause state and order status ---- */
  const stamp = $('stamp'), line = $('status-line');
  const LABEL = { pending: 'In the queue', ready: 'Ready', done: 'Picked up' };
  const MSG = { pending: "It's in. We'll email you when it's ready.", ready: "It's ready. Go grab it!", done: 'All done. Thanks!' };
  let shown = Array.from(stamp.classList).find((c) => LABEL[c]);

  Lazy.onState((st) => {
    readPalette();
    const paused = !!st.gamesPausedAll || !!(st.order && st.order.gamesPaused);
    if (paused !== blocked) {
      blocked = paused;
      if (blocked) hold();
      syncOverlay();
    }
    const o = st.order;
    if (o && o.status !== shown && LABEL[o.status]) {
      shown = o.status;
      stamp.textContent = LABEL[o.status];
      stamp.className = 'stamp ' + o.status;
      line.textContent = MSG[o.status];
      B.dataset.titleOverride = o.status === 'ready' ? 'READY' : '';
      document.title = (o.status === 'ready' ? 'READY | ' : '') + (B.dataset.site || document.title);
      if (o.status === 'ready' && navigator.vibrate) navigator.vibrate([200, 100, 200]);
    }
  });

  hint.textContent = Snake.hint;
  dpad.classList.add('on');
  syncOverlay();
  requestAnimationFrame(frame);
})();
