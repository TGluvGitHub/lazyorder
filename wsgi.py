"""Entry point for hosts that want a WSGI object (gunicorn, Passenger, PythonAnywhere, uWSGI)."""
from app import app

application = app  # Passenger / PythonAnywhere look for this name
