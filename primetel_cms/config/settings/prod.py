"""
Primetel CMS — Production Settings
"""
import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401, F403

# Never run production on the insecure fallback key from base.py.
if not os.environ.get("SECRET_KEY"):
    raise ImproperlyConfigured("SECRET_KEY must be set in the environment for production.")

# Render's load balancer appends the client address to X-Forwarded-For.
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=1)  # noqa: F405

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

# Static files — WhiteNoise with compression.
# Uploaded files (patient photos, encounter attachments) — Render's disk is
# wiped on every deploy, so production should point uploads at a private
# S3-compatible bucket (e.g. Supabase Storage). Files are always served
# through the login-protected /media/ view, never directly from the bucket.
MEDIA_S3_BUCKET = env("MEDIA_S3_BUCKET", default="")  # noqa: F405
if MEDIA_S3_BUCKET:
    DEFAULT_STORAGE = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": MEDIA_S3_BUCKET,
            "endpoint_url": env("MEDIA_S3_ENDPOINT_URL", default=None),  # noqa: F405
            "region_name": env("MEDIA_S3_REGION", default=None),  # noqa: F405
            "access_key": env("MEDIA_S3_ACCESS_KEY_ID", default=None),  # noqa: F405
            "secret_key": env("MEDIA_S3_SECRET_ACCESS_KEY", default=None),  # noqa: F405
            "default_acl": None,
            "querystring_auth": True,
            "file_overwrite": False,
            # Supabase's S3 endpoint needs path-style URLs (…/s3/<bucket>/<key>).
            "addressing_style": "path",
            "signature_version": "s3v4",
        },
    }
else:
    DEFAULT_STORAGE = {"BACKEND": "django.core.files.storage.FileSystemStorage"}

STORAGES = {
    "default": DEFAULT_STORAGE,
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
