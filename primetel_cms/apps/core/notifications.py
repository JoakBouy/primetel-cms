"""
Notification fan-out helpers.

Call `notify_role("LAB", ...)` to send to every active LAB user, or
`notify_user(user, ...)` for a specific person. Failures are swallowed —
notifications must never block the action that triggered them.
"""
from __future__ import annotations

from typing import Iterable

from django.contrib.auth import get_user_model

from .models import Notification


User = get_user_model()


def _create(recipient, *, kind, title, body="", url="", level="INFO",
            entity_type="", entity_id=None):
    try:
        Notification.objects.create(
            recipient=recipient,
            kind=kind,
            title=title,
            body=body,
            url=url,
            level=level,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        return True
    except Exception:
        # Never let a notification failure break the request.
        return False


def notify_user(user, **fields):
    """Send a notification to one user."""
    if user is None or not getattr(user, "is_authenticated", True):
        return False
    return _create(user, **fields)


def notify_role(role_codes: str | Iterable[str], *, exclude_actor=None, **fields):
    """Send a notification to every active user with one of the given role codes.

    `exclude_actor` is the user who triggered the event — they shouldn't be
    notified about their own action.
    """
    if isinstance(role_codes, str):
        role_codes = [role_codes]
    qs = User.objects.filter(is_active=True, role__code__in=list(role_codes))
    if exclude_actor is not None:
        qs = qs.exclude(pk=exclude_actor.pk)
    sent = 0
    for u in qs:
        if _create(u, **fields):
            sent += 1
    return sent


def unread_count(user) -> int:
    if user is None or not getattr(user, "is_authenticated", False):
        return 0
    return Notification.objects.filter(recipient=user, is_read=False).count()
