"""
Primetel CMS — Base Settings
Shared across all environments.
"""
import os
from pathlib import Path

import environ

# ──────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)
environ.Env.read_env(os.path.join(BASE_DIR, ".env"))

SECRET_KEY = env("SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# ──────────────────────────────────────────────
# Applications
# ──────────────────────────────────────────────
DJANGO_APPS = [
    "unfold",  # must be before django.contrib.admin
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "simple_history",
    "axes",
    "corsheaders",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.patients",
    "apps.appointments",
    "apps.encounters",
    "apps.pharmacy",
    "apps.lab",
    "apps.billing",
    "apps.reports",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ──────────────────────────────────────────────
# Middleware
# ──────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "apps.core.middleware.IdleSessionTimeoutMiddleware",
    "apps.core.middleware.AuditLogMiddleware",
    "axes.middleware.AxesMiddleware",
]

# ──────────────────────────────────────────────
# URL & WSGI
# ──────────────────────────────────────────────
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# ──────────────────────────────────────────────
# Templates
# ──────────────────────────────────────────────
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.core.context_processors.global_context",
            ],
        },
    },
]

# ──────────────────────────────────────────────
# Database — overridden in dev.py / prod.py
# ──────────────────────────────────────────────
DATABASES = {
    "default": env.db("DATABASE_URL", default="sqlite:///db.sqlite3"),
}

# ──────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/login/"

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ──────────────────────────────────────────────
# Sessions
# ──────────────────────────────────────────────
SESSION_COOKIE_AGE = 28800  # 8 hours (one clinic shift) — absolute upper bound,
# enforced server-side by IdleSessionTimeoutMiddleware from the session start.
SESSION_SAVE_EVERY_REQUEST = False
# Idle re-authentication window. Shorter than SESSION_COOKIE_AGE: protects
# shared workstations where a user walks away mid-shift. Background HTMX polls
# do not count as activity.
IDLE_SESSION_SECONDS = env.int("IDLE_SESSION_SECONDS", default=15 * 60)

# Number of reverse proxies in front of the app that append to
# X-Forwarded-For (Render's load balancer = 1). 0 means trust REMOTE_ADDR only.
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=0)

# ──────────────────────────────────────────────
# Internationalisation
# ──────────────────────────────────────────────
LANGUAGE_CODE = "sw"
LANGUAGES = [
    ("sw", "Kiswahili"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True

TIME_ZONE = "Africa/Nairobi"
USE_TZ = True

# Date/time display formats
DATE_FORMAT = "d/m/Y"
DATETIME_FORMAT = "d/m/Y H:i"
SHORT_DATE_FORMAT = "d/m/Y"

# ──────────────────────────────────────────────
# Static & Media files
# ──────────────────────────────────────────────
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    # "default" must be listed explicitly: overriding STORAGES replaces the
    # whole dict, and without it every file upload fails.
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# File upload limits
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# Allowed upload extensions
ALLOWED_UPLOAD_EXTENSIONS = [".jpg", ".jpeg", ".png", ".pdf"]

# ──────────────────────────────────────────────
# Django REST Framework
# ──────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}

# ──────────────────────────────────────────────
# Django Axes (Login rate limiting)
# ──────────────────────────────────────────────
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 0.25  # 15 minutes in hours
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
# Resolve the client IP with the same proxy-aware logic as the audit log, so
# lockouts can't be dodged with a forged X-Forwarded-For header.
AXES_CLIENT_IP_CALLABLE = "apps.core.utils.get_client_ip"

# ──────────────────────────────────────────────
# Factory reset (admin "danger zone")
# ──────────────────────────────────────────────
# Off unless explicitly enabled. Turn on only for a pre-go-live wipe of test
# data, then turn it off again.
ALLOW_FACTORY_RESET = env.bool("ALLOW_FACTORY_RESET", default=False)

# ──────────────────────────────────────────────
# Django Simple History
# ──────────────────────────────────────────────
SIMPLE_HISTORY_HISTORY_ID_USE_UUID = True

# ──────────────────────────────────────────────
# Security — base; hardened in prod.py
# ──────────────────────────────────────────────
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"

# ──────────────────────────────────────────────
# Default primary key field type
# ──────────────────────────────────────────────
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ──────────────────────────────────────────────
# Django Unfold Admin
# ──────────────────────────────────────────────
UNFOLD = {
    "SITE_TITLE": "Primetel CMS",
    "SITE_HEADER": "Primetel CMS Admin",
    # Sidebar customisation — adds a 'System' section with the factory reset
    # link. The link is server-side-gated (ALLOW_FACTORY_RESET + superuser /
    # ADMIN role) inside the view itself; the `permission` callable here keeps
    # it from rendering for non-eligible users so they never see it.
    "SIDEBAR": {
        "show_search": True,
        "navigation": [
            {
                "title": "System",
                "separator": True,
                "items": [
                    {
                        "title": "⚠ Factory reset",
                        "icon": "delete_forever",
                        "link": "/admin/system/factory-reset/",
                        "permission": "apps.core.admin.can_use_factory_reset",
                    },
                ],
            },
        ],
    },
}
