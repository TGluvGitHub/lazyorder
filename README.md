# Lazyorder

Pick things from the preparer's menu (with amounts, no prices), send the order to the kitchen by email,
play Snake or Flappy while waiting, and get an email when the food is ready.

## 1. Fill in `.env`
Open `.env` and fill in the four values (it explains each one):
`SENDING_EMAIL`, `GMAIL_APP_PASSWORD`, `PREPARER_EMAIL`, `PREPARER_PASSWORD`.
The app sends mail through Gmail using an app password (Google account > Security > 2-Step Verification > App passwords).

## 2. Run it
    pip install -r requirements.txt
    python app.py                       # try it at http://127.0.0.1:5000

On your website, point your host at `wsgi.py` (object name `application`, or `wsgi:application` for gunicorn):

    gunicorn -w 2 -b 127.0.0.1:8000 wsgi:application

then proxy your domain (or a path like /lazyorder) to port 8000 in nginx/Apache. cPanel "Setup Python App" and
PythonAnywhere also work: set the startup file to `wsgi.py`. If the site is https, uncomment `HTTPS=1` in `.env`.
Keep `.env` and `data/` outside any folder the web server serves directly.

## 3. Use it
- Customers: your site's home page. They get a page for their order with the games.
- You: `/kitchen`, log in with PREPARER_EMAIL and PREPARER_PASSWORD. From there you can
  mark orders ready/picked up, pause games for one person or everyone, post announcements,
  edit the menu, change the site name, emoji and colours, and remove leaderboard entries.

Data lives in `data/lazyorder.db`. Back it up if you care about the scores.
