"""Lazyorder: pick what you want, send it to the kitchen, play something while you wait.

Everything lives in this one file on purpose. Run it with `python app.py` for
local testing, or point your host at wsgi.py (see README.md).
"""
from __future__ import annotations

import hmac
import html
import logging
import os
import re
import secrets
import smtplib
import sqlite3
import threading
import time
from datetime import timedelta
from email.headerregistry import Address
from email.message import EmailMessage
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("lazyorder")

DATA_DIR = Path(os.getenv("LAZYORDER_DATA") or BASE_DIR / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "lazyorder.db"


def env(name: str) -> str:
    return (os.getenv(name) or "").strip()


# --------------------------------------------------------------------------
# Defaults and colour helpers
# --------------------------------------------------------------------------

DEFAULTS = {
    "site_name": "Lazyorder",
    "emoji": "🛋️",
    "color_bg": "#f0e4c8",
    "color_ink": "#2a2018",
    "color_accent": "#c4402a",
    "announcement": "",
    "announcement_id": "0",
    "games_paused": "0",
}

PRESETS = [
    ("Diner", "#f0e4c8", "#2a2018", "#c4402a"),
    ("Mint", "#dcebe2", "#12271e", "#1c7656"),
    ("Night shift", "#1c1b18", "#efe6d2", "#e8a33d"),
    ("Blueprint", "#d9e6f2", "#13233a", "#2a5ea8"),
    ("Strawberry milk", "#f6dde3", "#3a1d27", "#cf3f78"),
    ("Newsprint", "#e9e9e4", "#161616", "#e5322d"),
]

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
# No commas, semicolons, quotes or brackets: one address, nothing sneaky.
EMAIL = re.compile(r"^[^\s@,;<>()\[\]\"'\\]+@[^\s@,;<>()\[\]\"'\\]+\.[^\s@,;<>()\[\]\"'\\]+$")


def _rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def mix(a: str, b: str, t: float) -> str:
    return _hex(tuple(x + (y - x) * t for x, y in zip(_rgb(a), _rgb(b))))


def luminance(h: str) -> float:
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g_, b = (lin(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g_ + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def theme_vars(s: dict) -> dict:
    """Every CSS variable the site uses, derived from the three colours the preparer picks."""
    bg, ink, acc = s["color_bg"], s["color_ink"], s["color_accent"]
    dark = luminance(bg) < 0.35
    paper = mix(bg, ink, 0.09) if dark else mix(bg, "#ffffff", 0.6)
    on_accent = max(("#ffffff", "#161616"), key=lambda c: contrast(c, acc))
    return {
        "--bg": bg,
        "--ink": ink,
        "--accent": acc,
        "--on-accent": on_accent,
        "--paper": paper,
        "--line": mix(bg, ink, 0.32),
        "--muted": mix(ink, bg, 0.42),
    }


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS menu(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    max_qty INTEGER NOT NULL DEFAULT 10,
    available INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    games_paused INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    ready_at INTEGER,
    notify_status TEXT NOT NULL DEFAULT 'sending',
    ready_email_status TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS order_items(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    qty INTEGER NOT NULL,
    self_serve INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS scores(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game TEXT NOT NULL,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    score INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(game, name_key));
"""

GAMES = {"snake": 324, "flappy": 1000}  # game -> highest score we'll believe


def db_connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db() -> None:
    con = db_connect()
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(SCHEMA)
    if "self_serve" not in [r[1] for r in con.execute("PRAGMA table_info(order_items)")]:
        con.execute("ALTER TABLE order_items ADD COLUMN self_serve INTEGER NOT NULL DEFAULT 0")
    con.commit()
    con.close()


init_db()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = db_connect()
    return g.db


def get_settings() -> dict:
    if "settings" not in g:
        rows = get_db().execute("SELECT key, value FROM settings").fetchall()
        s = dict(DEFAULTS)
        s.update({r["key"]: r["value"] for r in rows})
        # Never let a bad stored value reach the stylesheet.
        for k in ("color_bg", "color_ink", "color_accent"):
            if not HEX.match(s[k]):
                s[k] = DEFAULTS[k]
        g.settings = s
    return g.settings


def set_setting(key: str, value: str) -> None:
    db = get_db()
    db.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    db.commit()
    g.pop("settings", None)


# --------------------------------------------------------------------------
# App setup
# --------------------------------------------------------------------------

def _load_secret() -> str:
    path = DATA_DIR / "secret_key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    return path.read_text().strip()


app = Flask(__name__)
app.secret_key = _load_secret()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    MAX_CONTENT_LENGTH=64 * 1024,
)
if env("HTTPS") == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.config["SESSION_COOKIE_SECURE"] = True

PLACEHOLDERS = {"change-me", "changeme", "abcd efgh ijkl mnop"}


def config_problems() -> list[str]:
    out = []
    if not env("SENDING_EMAIL") or not EMAIL.match(env("SENDING_EMAIL")):
        out.append("SENDING_EMAIL is missing or isn't an email address.")
    if not env("GMAIL_APP_PASSWORD") or env("GMAIL_APP_PASSWORD").lower() in PLACEHOLDERS:
        out.append("GMAIL_APP_PASSWORD is empty, so no emails can be sent.")
    if not env("PREPARER_EMAIL") or not EMAIL.match(env("PREPARER_EMAIL")):
        out.append("PREPARER_EMAIL is missing or isn't an email address.")
    if not env("PREPARER_PASSWORD") or env("PREPARER_PASSWORD").lower().startswith("change-me"):
        out.append("PREPARER_PASSWORD is not set.")
    return out


for _p in config_problems():
    log.warning("Lazyorder .env: %s", _p)


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.template_global()
def csrf() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.template_global()
def asset(name: str) -> str:
    """Static file URL with a version stamp so uploads of new CSS/JS show up right away."""
    try:
        v = int((BASE_DIR / "static" / name).stat().st_mtime)
    except OSError:
        v = 0
    return url_for("static", filename=name, v=v)


def _announcement(s: dict) -> dict:
    return {"id": int(s["announcement_id"] or 0), "text": s["announcement"]}


@app.context_processor
def inject_site():
    s = get_settings()
    return {
        "site": {"name": s["site_name"], "emoji": s["emoji"]},
        "theme": theme_vars(s),
        "announcement": _announcement(s),
    }


@app.after_request
def headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if request.path.startswith(("/api/", "/kitchen")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------

_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()


def rl_count(key: str, window: float) -> int:
    now = time.time()
    with _hits_lock:
        q = [t for t in _hits.get(key, []) if now - t < window]
        _hits[key] = q
        return len(q)


def rl_add(key: str) -> None:
    with _hits_lock:
        _hits.setdefault(key, []).append(time.time())


def clean_text(value: str, limit: int) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]+", " ", value or "")
    return re.sub(r" {2,}", " ", value).strip()[:limit]


def run_bg(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def now() -> int:
    return int(time.time())


# --------------------------------------------------------------------------
# Email (Gmail SMTP with an app password)
# --------------------------------------------------------------------------

def send_mail(to: str, subject: str, body: str, from_name: str, reply_to: str | None = None) -> None:
    user = env("SENDING_EMAIL")
    pw = env("GMAIL_APP_PASSWORD").replace(" ", "")
    if not user or not pw or pw.lower() in {p.replace(" ", "") for p in PLACEHOLDERS}:
        raise RuntimeError("SENDING_EMAIL or GMAIL_APP_PASSWORD is missing in .env")
    if not EMAIL.match(to):
        raise ValueError("bad recipient address")
    msg = EmailMessage()
    msg["From"] = Address(display_name=clean_text(from_name, 60), addr_spec=user)
    msg["To"] = Address(addr_spec=to)
    if reply_to and EMAIL.match(reply_to):
        msg["Reply-To"] = Address(addr_spec=reply_to)
    msg["Subject"] = clean_text(subject, 150)
    msg.set_content(body)
    host = os.getenv("LAZYORDER_SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("LAZYORDER_SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
        smtp.login(user, pw)
        smtp.send_message(msg, to_addrs=[to])


def explain(exc: Exception) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "failed: Gmail refused the login. Check SENDING_EMAIL and GMAIL_APP_PASSWORD in .env."
    if isinstance(exc, (OSError, smtplib.SMTPConnectError)):
        return "failed: couldn't reach Gmail from this server."
    return ("failed: " + str(exc))[:160]


def _item_lines(items) -> str:
    return "\n".join(f"  {i['qty']} x {i['name']}" + ("  (serving themselves)" if i["self_serve"] else "")
                     for i in items)


def notify_new_order(order_id: int, kitchen_url: str, site_name: str) -> None:
    con = db_connect()
    try:
        o = con.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        items = con.execute("SELECT name, qty, self_serve FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
        body = (f"{o['name']} ({o['email']}) just ordered:\n\n{_item_lines(items)}\n")
        if o["note"]:
            body += f"\nNote: {o['note']}\n"
        body += f"\nMark it ready here:\n{kitchen_url}\n\nSent by {site_name}\n"
        try:
            send_mail(env("PREPARER_EMAIL"), f"New order #{o['id']} from {o['name']}", body,
                      site_name, reply_to=o["email"])
            status = "sent"
        except Exception as exc:  # noqa: BLE001 - we want to show every kind of failure
            log.warning("order email failed: %s", exc)
            status = explain(exc)
        con.execute("UPDATE orders SET notify_status = ? WHERE id = ?", (status, order_id))
        con.commit()
    finally:
        con.close()


def notify_ready(order_id: int, site_name: str) -> None:
    con = db_connect()
    try:
        o = con.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        items = con.execute("SELECT name, qty, self_serve FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
        body = (f"Hi {o['name']},\n\nYour order is ready:\n\n{_item_lines(items)}\n\n"
                f"Come and get it!\n\n{site_name}\n")
        try:
            send_mail(o["email"], f"Your order is ready ({site_name})", body, site_name,
                      reply_to=env("PREPARER_EMAIL"))
            status = "sent"
        except Exception as exc:  # noqa: BLE001
            log.warning("ready email failed: %s", exc)
            status = explain(exc)
        con.execute("UPDATE orders SET ready_email_status = ? WHERE id = ?", (status, order_id))
        con.commit()
    finally:
        con.close()


# --------------------------------------------------------------------------
# Public pages
# --------------------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    db = get_db()
    items = db.execute("SELECT * FROM menu ORDER BY sort, id").fetchall()
    errors: list[str] = []
    form = {"name": "", "email": "", "note": ""}
    qty: dict[int, int] = {}
    selfs: dict[int, bool] = {}

    if request.method == "POST":
        if request.form.get("website"):  # honeypot: real people never see this field
            return redirect(url_for("index"))
        form = {k: request.form.get(k, "") for k in form}
        name = clean_text(form["name"], 30)
        email = form["email"].strip()
        note = clean_text(form["note"], 200)

        chosen = []
        for it in items:
            try:
                n = int(request.form.get(f"qty_{it['id']}", "0") or 0)
            except ValueError:
                n = 0
            qty[it["id"]] = max(0, n)
            mine = bool(request.form.get(f"self_{it['id']}"))
            selfs[it["id"]] = mine
            if n <= 0:
                continue
            if not it["available"]:
                errors.append(f"{it['name']} is sold out.")
            elif n > it["max_qty"]:
                errors.append(f"{it['name']}: {it['max_qty']} at most, please.")
            else:
                chosen.append((it["name"], n, int(mine)))
        if not chosen and not errors:
            errors.append("Pick at least one thing.")
        if not name:
            errors.append("Put your name on the slip.")
        if not EMAIL.match(email) or len(email) > 120:
            errors.append("That email doesn't look right.")
        ip = request.remote_addr or "?"
        if not errors and rl_count("order:" + ip, 900) >= 5:
            errors.append("That's a lot of orders. Give it a few minutes.")

        if not errors:
            rl_add("order:" + ip)
            token = secrets.token_urlsafe(12)
            cur = db.execute(
                "INSERT INTO orders(token, name, email, note, created_at) VALUES(?,?,?,?,?)",
                (token, name, email, note, now()))
            oid = cur.lastrowid
            db.executemany("INSERT INTO order_items(order_id, name, qty, self_serve) VALUES(?,?,?,?)",
                           [(oid, n, q, sv) for n, q, sv in chosen])
            db.commit()
            run_bg(notify_new_order, oid, url_for("kitchen", _external=True),
                   get_settings()["site_name"])
            return redirect(url_for("status", token=token))

    return render_template("index.html", items=items, errors=errors, form=form, qty=qty, selfs=selfs,
                           page_title=""), (400 if errors else 200)


def _get_order(token: str):
    db = get_db()
    o = db.execute("SELECT * FROM orders WHERE token = ?", (token,)).fetchone()
    if not o:
        abort(404)
    items = db.execute("SELECT name, qty, self_serve FROM order_items WHERE order_id = ? ORDER BY id",
                       (o["id"],)).fetchall()
    return o, items


@app.get("/order/<token>")
def status(token):
    o, items = _get_order(token)
    return render_template("status.html", o=o, items=items, page_title=f"Order #{o['id']}")


@app.get("/scores")
def scores():
    return render_template("scores.html", page_title="Scores")


@app.get("/favicon.svg")
def favicon():
    e = html.escape(get_settings()["emoji"] or "🍽️")
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           f'<text y=".9em" font-size="90">{e}</text></svg>')
    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico")
def favicon_ico():
    return redirect(url_for("favicon"), code=302)


# --------------------------------------------------------------------------
# Public JSON used by the pages' little bits of JavaScript
# --------------------------------------------------------------------------

def boards() -> dict:
    db = get_db()
    out = {}
    for game in GAMES:
        rows = db.execute("SELECT name, score FROM scores WHERE game = ? "
                          "ORDER BY score DESC, created_at ASC LIMIT 10", (game,)).fetchall()
        out[game] = [{"name": r["name"], "score": r["score"]} for r in rows]
    return out


@app.get("/api/state")
def api_state():
    s = get_settings()
    out = {
        "site": {"name": s["site_name"], "emoji": s["emoji"]},
        "theme": theme_vars(s),
        "announcement": _announcement(s),
        "gamesPausedAll": s["games_paused"] == "1",
    }
    token = request.args.get("t", "")
    if token:
        o = get_db().execute("SELECT id, name, status, games_paused FROM orders WHERE token = ?",
                             (token,)).fetchone()
        if o:
            out["order"] = {"number": o["id"], "name": o["name"], "status": o["status"],
                            "gamesPaused": bool(o["games_paused"])}
    return jsonify(out)


@app.get("/api/scores")
def api_scores():
    return jsonify(boards())


@app.post("/api/score")
def api_score():
    data = request.get_json(silent=True) or {}
    game = data.get("game")
    try:
        value = int(data.get("score"))
    except (TypeError, ValueError):
        return jsonify(error="bad score"), 400
    if game not in GAMES or not (1 <= value <= GAMES[game]) or (game == "snake" and value < 3):
        return jsonify(error="bad score"), 400
    db = get_db()
    o = db.execute("SELECT name, games_paused FROM orders WHERE token = ?",
                   (str(data.get("t", "")),)).fetchone()
    if not o:
        return jsonify(error="unknown order"), 403
    if o["games_paused"] or get_settings()["games_paused"] == "1":
        return jsonify(error="games are paused"), 423
    key = o["name"].lower()
    prev = db.execute("SELECT score FROM scores WHERE game = ? AND name_key = ?", (game, key)).fetchone()
    best = prev is None or value > prev["score"]
    if best:
        db.execute("INSERT INTO scores(game, name, name_key, score, created_at) VALUES(?,?,?,?,?) "
                   "ON CONFLICT(game, name_key) DO UPDATE SET score = excluded.score, "
                   "created_at = excluded.created_at, name = excluded.name",
                   (game, o["name"], key, value, now()))
        db.commit()
    return jsonify(ok=True, best=best, boards=boards())


# --------------------------------------------------------------------------
# Kitchen (the preparer's side)
# --------------------------------------------------------------------------

@app.before_request
def csrf_protect():
    if request.method == "POST" and request.path.startswith("/kitchen") \
            and request.endpoint != "kitchen_login":
        sent = request.headers.get("X-CSRF") or request.form.get("csrf") or ""
        have = session.get("csrf", "")
        if not sent or not have or not hmac.compare_digest(sent, have):
            abort(400)


def kitchen_only(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("kitchen"):
            if request.path.startswith("/kitchen/api/"):
                return jsonify(error="login required"), 401
            return redirect(url_for("kitchen_login"))
        return fn(*a, **kw)
    return wrapper


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


@app.route("/kitchen/login", methods=["GET", "POST"])
def kitchen_login():
    ready = bool(env("PREPARER_EMAIL")) and bool(env("PREPARER_PASSWORD")) \
        and not env("PREPARER_PASSWORD").lower().startswith("change-me")
    error = ""
    ip = request.remote_addr or "?"
    if request.method == "POST":
        if not ready:
            error = "Login isn't set up yet. Fill in PREPARER_EMAIL and PREPARER_PASSWORD in .env."
        elif rl_count("login:" + ip, 600) >= 8:
            error = "Too many tries. Wait ten minutes."
        else:
            email_ok = _same(request.form.get("email", "").strip().lower(), env("PREPARER_EMAIL").lower())
            pw_ok = _same(request.form.get("password", ""), env("PREPARER_PASSWORD"))
            if email_ok and pw_ok:
                session.clear()
                session["kitchen"] = True
                session["csrf"] = secrets.token_hex(16)
                session.permanent = True
                return redirect(url_for("kitchen"))
            rl_add("login:" + ip)
            error = "Wrong email or password."
    return render_template("login.html", error=error, ready=ready, page_title="Kitchen login"), \
        (401 if error else 200)


@app.post("/kitchen/logout")
def kitchen_logout():
    session.clear()
    return redirect(url_for("index"))


@app.get("/kitchen")
@kitchen_only
def kitchen():
    db = get_db()
    return render_template(
        "kitchen.html", page_title="Kitchen",
        problems=config_problems(),
        menu=db.execute("SELECT * FROM menu ORDER BY sort, id").fetchall(),
        s=get_settings(), presets=PRESETS,
        scoreboard={game: db.execute(
            "SELECT id, name, score FROM scores WHERE game = ? ORDER BY score DESC, created_at ASC LIMIT 30",
            (game,)).fetchall() for game in GAMES},
        emoji_choices="🛋️ 🦥 🍔 🍕 🌮 🍜 ☕ 🥪 🍩 🧁 🥐 🐢 🍣 🥗".split())


def _orders_payload() -> dict:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM orders WHERE status IN ('pending','ready') "
        "UNION ALL SELECT * FROM (SELECT * FROM orders WHERE status = 'done' ORDER BY id DESC LIMIT 12) "
        "ORDER BY id").fetchall()
    ids = [r["id"] for r in rows]
    by_order: dict[int, list] = {i: [] for i in ids}
    if ids:
        q = ",".join("?" * len(ids))
        for it in db.execute(f"SELECT order_id, name, qty, self_serve FROM order_items WHERE order_id IN ({q}) ORDER BY id", ids):
            by_order[it["order_id"]].append({"name": it["name"], "qty": it["qty"], "selfServe": bool(it["self_serve"])})
    return {
        "pausedAll": get_settings()["games_paused"] == "1",
        "orders": [{
            "id": r["id"], "name": r["name"], "email": r["email"], "note": r["note"],
            "status": r["status"], "gamesPaused": bool(r["games_paused"]),
            "createdAt": r["created_at"], "readyAt": r["ready_at"],
            "notifyStatus": r["notify_status"], "readyEmailStatus": r["ready_email_status"],
            "items": by_order[r["id"]],
        } for r in rows],
    }


@app.get("/kitchen/api/orders")
@kitchen_only
def kitchen_orders():
    return jsonify(_orders_payload())


@app.post("/kitchen/api/orders/<int:oid>/<action>")
@kitchen_only
def kitchen_order_action(oid, action):
    db = get_db()
    o = db.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    if not o:
        return jsonify(error="no such order"), 404
    site_name = get_settings()["site_name"]
    if action == "ready":
        cur = db.execute("UPDATE orders SET status = 'ready', ready_at = ?, ready_email_status = 'sending' "
                         "WHERE id = ? AND status = 'pending'", (now(), oid))
        db.commit()
        if cur.rowcount:
            run_bg(notify_ready, oid, site_name)
    elif action == "resend":
        db.execute("UPDATE orders SET ready_email_status = 'sending' WHERE id = ?", (oid,))
        db.commit()
        run_bg(notify_ready, oid, site_name)
    elif action == "done":
        db.execute("UPDATE orders SET status = 'done' WHERE id = ?", (oid,))
        db.commit()
    elif action == "games":
        paused = bool((request.get_json(silent=True) or {}).get("paused"))
        db.execute("UPDATE orders SET games_paused = ? WHERE id = ?", (int(paused), oid))
        db.commit()
    else:
        return jsonify(error="unknown action"), 400
    return jsonify(ok=True)


@app.post("/kitchen/api/games-all")
@kitchen_only
def kitchen_games_all():
    paused = bool((request.get_json(silent=True) or {}).get("paused"))
    set_setting("games_paused", "1" if paused else "0")
    return jsonify(ok=True, pausedAll=paused)


@app.post("/kitchen/announce")
@kitchen_only
def kitchen_announce():
    text = clean_text(request.form.get("text", ""), 200)
    if request.form.get("clear"):
        text = ""
    nid = int(get_settings()["announcement_id"] or 0) + 1  # new id = shows again even for people who closed the old one
    set_setting("announcement", text)
    set_setting("announcement_id", str(nid))
    flash("Announcement sent." if text else "Announcement cleared.")
    return redirect(url_for("kitchen") + "#talk")


@app.post("/kitchen/look")
@kitchen_only
def kitchen_look():
    name = clean_text(request.form.get("site_name", ""), 40)
    emoji = clean_text(request.form.get("emoji", ""), 12)
    colors = {k: request.form.get(k, "").strip() for k in ("color_bg", "color_ink", "color_accent")}
    if not name:
        flash("The site needs a name.")
    elif not all(HEX.match(v) for v in colors.values()):
        flash("Those colours didn't come through right. Nothing saved.")
    else:
        set_setting("site_name", name)
        set_setting("emoji", emoji)
        for k, v in colors.items():
            set_setting(k, v.lower())
        flash("Saved. Everyone's pages update within a few seconds.")
        if contrast(colors["color_bg"], colors["color_ink"]) < 3.5:
            flash("Heads up: the text and background colours are close, so it may be hard to read.")
    return redirect(url_for("kitchen") + "#look")


def _menu_fields():
    name = clean_text(request.form.get("name", ""), 60)
    try:
        max_qty = max(1, min(50, int(request.form.get("max_qty", "10"))))
    except ValueError:
        max_qty = 10
    return name, max_qty


@app.post("/kitchen/menu/add")
@kitchen_only
def kitchen_menu_add():
    name, max_qty = _menu_fields()
    if name:
        db = get_db()
        nxt = db.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM menu").fetchone()[0]
        db.execute("INSERT INTO menu(name, max_qty, sort) VALUES(?,?,?)", (name, max_qty, nxt))
        db.commit()
    else:
        flash("Give the item a name.")
    return redirect(url_for("kitchen") + "#menu")


@app.post("/kitchen/menu/<int:mid>/save")
@kitchen_only
def kitchen_menu_save(mid):
    name, max_qty = _menu_fields()
    if name:
        db = get_db()
        db.execute("UPDATE menu SET name = ?, max_qty = ?, available = ? WHERE id = ?",
                   (name, max_qty, 1 if request.form.get("available") else 0, mid))
        db.commit()
    return redirect(url_for("kitchen") + "#menu")


@app.post("/kitchen/menu/<int:mid>/delete")
@kitchen_only
def kitchen_menu_delete(mid):
    db = get_db()
    db.execute("DELETE FROM menu WHERE id = ?", (mid,))
    db.commit()
    return redirect(url_for("kitchen") + "#menu")


@app.post("/kitchen/menu/<int:mid>/move")
@kitchen_only
def kitchen_menu_move(mid):
    db = get_db()
    rows = [r["id"] for r in db.execute("SELECT id FROM menu ORDER BY sort, id")]
    if mid in rows:
        i = rows.index(mid)
        j = i - 1 if request.form.get("dir") == "up" else i + 1
        if 0 <= j < len(rows):
            rows[i], rows[j] = rows[j], rows[i]
            db.executemany("UPDATE menu SET sort = ? WHERE id = ?", [(n, r) for n, r in enumerate(rows)])
            db.commit()
    return redirect(url_for("kitchen") + "#menu")


@app.post("/kitchen/scores/<int:sid>/delete")
@kitchen_only
def kitchen_score_delete(sid):
    db = get_db()
    db.execute("DELETE FROM scores WHERE id = ?", (sid,))
    db.commit()
    return redirect(url_for("kitchen") + "#scores")


@app.post("/kitchen/scores/clear")
@kitchen_only
def kitchen_scores_clear():
    game = request.form.get("game")
    if game in GAMES:
        db = get_db()
        db.execute("DELETE FROM scores WHERE game = ?", (game,))
        db.commit()
        flash(f"{game.title()} leaderboard cleared.")
    return redirect(url_for("kitchen") + "#scores")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host="127.0.0.1", port=int(env("PORT") or 5000), debug=False)
