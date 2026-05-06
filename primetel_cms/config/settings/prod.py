"""
Primetel CMS — Production Settings
"""
import os

from .base import *  # noqa: F401, F403

# ─── Sentry (optional) ────────────────────────────────────────────
SENTRY_DSN = env("SENTRY_DSN", default="")  # noqa: F405
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[DjangoIntegration()],
            traces_sample_rate=float(env("SENTRY_TRACES_SAMPLE_RATE", default="0.0")),  # noqa: F405
            send_default_pii=False,  # do not include PHI in error reports
            environment=env("SENTRY_ENV", default="production"),  # noqa: F405
        )
    except ImportError:
        # sentry-sdk is optional; skip gracefully if not installed.
        pass

DEBUG = False

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["primetel-cms-oz37.onrender.com", "cms.primetel.tech"],
)  # noqa: F405

# Database — PostgreSQL via Supabase
DATABASES = {
    "default": env.db("DATABASE_URL"),  # noqa: F405
}
DATABASES["default"]["OPTIONS"] = {
    "sslmode": "require",
}

# Security hardening
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = False  # Allow JavaScript (HTMX) to read the CSRF cookie
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Static files — WhiteNoise with compression
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Render external hostname — must be added BEFORE CSRF_TRUSTED_ORIGINS is computed
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

# CORS — derive from ALLOWED_HOSTS so they stay in sync.
CORS_ALLOWED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS if host and host != "*"]

# CSRF trusted origins — required by Django 4+ for HTTPS form POSTs behind
# a proxy. Without this, every POST returns "CSRF verification failed".
# Computed AFTER RENDER_EXTERNAL_HOSTNAME so it includes all hosts.
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS if host and host != "*"]

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
