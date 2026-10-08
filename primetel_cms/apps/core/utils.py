"""
Shared request helpers.

- strip_language_prefix: "/en/patients/x/" -> "/patients/x/" so path checks
  work the same for every UI language (i18n_patterns adds the prefix for
  non-default languages).
- get_client_ip: proxy-aware client IP that can't be spoofed by a client
  sending its own X-Forwarded-For header.
- audit: one AuditLog writer for every app (replaces per-app copies).
- parse_uuid: tolerant UUID parsing for query/POST values.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import uuid
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _language_prefix_re():
    codes = "|".join(re.escape(code) for code, _ in settings.LANGUAGES)
    return re.compile(rf"^/(?:{codes})(?=/|$)")


def strip_language_prefix(path: str) -> str:
    """Remove a leading language segment ("/en", "/sw") from a URL path."""
    stripped = _language_prefix_re().sub("", path or "", count=1)
    return stripped or "/"


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except (ValueError, AttributeError):
        return None


def get_client_ip(request) -> str | None:
    """Return the real client IP.

    Behind N trusted reverse proxies (settings.TRUSTED_PROXY_COUNT), each proxy
    appends the address it received the request from to X-Forwarded-For. The
    client controls everything to the left of those entries, so we take the
    N-th entry from the right — never the left-most one.
    """
    proxy_count = int(getattr(settings, "TRUSTED_PROXY_COUNT", 0) or 0)
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if proxy_count > 0 and forwarded:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            candidate = hops[-proxy_count] if len(hops) >= proxy_count else hops[0]
            ip = _valid_ip(candidate)
            if ip:
                return ip
    return _valid_ip(request.META.get("REMOTE_ADDR", ""))


def audit(request, action, obj=None, *, entity_type=None, entity_id=None, **metadata):
    """Write an AuditLog row. Never raises — auditing must not block the action."""
    from .models import AuditLog

    try:
        actor = getattr(request, "user", None) if request is not None else None
        if actor is not None and not actor.is_authenticated:
            actor = None
        AuditLog.objects.create(
            actor=actor,
            action=action,
            entity_type=entity_type or (obj.__class__.__name__ if obj is not None else "System"),
            entity_id=entity_id if entity_id is not None else getattr(obj, "pk", None),
            metadata=metadata,
            ip_address=get_client_ip(request) if request is not None else None,
            user_agent=(request.META.get("HTTP_USER_AGENT", "")[:500] if request is not None else ""),
        )
    except Exception:
        logger.exception("Failed to write audit log entry (%s)", action)


def parse_uuid(value) -> uuid.UUID | None:
    """Return a UUID for a well-formed string, else None (no exception)."""
    if not value:
        return None
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, AttributeError, TypeError):
        return None
