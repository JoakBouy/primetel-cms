"""
Primetel CMS — Development Settings
"""
from .base import *  # noqa: F401, F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Use SQLite for local development
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}

# Disable SSL in dev
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# WhiteNoise in dev — serve static files
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

# Email to console in dev
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Axes — disable in dev for convenience
AXES_ENABLED = False

# CORS — allow all in dev
CORS_ALLOW_ALL_ORIGINS = True
