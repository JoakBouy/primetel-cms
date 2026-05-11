"""
Primetel CMS — Core Views.
System-level views: CSRF failure page, health check, notification bell.
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from .models import Notification


# Session key tracking when the bell client last polled. Anything newer than
# this is a "fresh" notification we should pop as a toast on the next poll.
_LAST_POLL_KEY = "_notif_last_poll"


def csrf_failure(request, reason=""):
    """Custom CSRF failure view.

    Shows a user-friendly page instead of the raw Django 403 so that clinic
    staff know to reload the page rather than panic.
    """
    html = render_to_string("accounts/csrf_failed.html", {
        "reason": reason,
    }, request=request)
    return HttpResponseForbidden(html)


# ─── Notification bell ─────────────────────────────────────────

@login_required
def notifications_panel(request):
    """HTMX-loaded bell dropdown body. Returns the latest 15 notifications."""
    notifs = (
        Notification.objects.filter(recipient=request.user)
        .order_by("-created_at")[:15]
    )
    unread = Notification.objects.filter(recipient=request.user, is_read=False).count()
    return render(request, "core/_notification_panel.html", {
        "notifications": notifs,
        "unread_count": unread,
    })


@login_required
def notifications_badge(request):
    """HTMX poll target: refreshes the badge count and pops any new
    notifications since the previous poll as toasts.

    The session stores the timestamp of the last poll. On each call we find
    notifications created after that marker and emit them via the HX-Trigger
    response header — HTMX will dispatch each as a custom DOM event the
    front-end's toastManager listens for.
    """
    now = timezone.now()
    last_poll_iso = request.session.get(_LAST_POLL_KEY)
    last_poll = parse_datetime(last_poll_iso) if last_poll_iso else None

    count = Notification.objects.filter(recipient=request.user, is_read=False).count()

    # Find unread notifications that arrived AFTER the last poll. We cap at 5
    # toasts per poll so a sudden burst doesn't carpet-bomb the UI.
    fresh_qs = Notification.objects.filter(
        recipient=request.user, is_read=False,
    )
    if last_poll is not None:
        fresh_qs = fresh_qs.filter(created_at__gt=last_poll)
    else:
        # First poll of this session — don't pop everything in the inbox as
        # toasts, just from this moment on.
        fresh_qs = fresh_qs.none()
    fresh = list(fresh_qs.order_by("-created_at")[:5])

    request.session[_LAST_POLL_KEY] = now.isoformat()

    response = render(request, "core/_notification_badge.html", {"unread_count": count})

    if fresh:
        # HX-Trigger payload: each notification fires a 'notify-toast' event with
        # its level/title/body/url. The client's toastManager turns these into
        # auto-dismissing toasts.
        triggers = {"notify-toast": [
            {
                "id": str(n.pk),
                "level": (n.level or "INFO").lower(),
                "title": n.title,
                "body": n.body or "",
                "url": n.url or "",
            }
            for n in fresh
        ]}
        response["HX-Trigger"] = json.dumps(triggers)
    return response


@require_POST
@login_required
def notifications_mark_read(request, pk):
    """Mark a single notification read and return the refreshed panel body."""
    notif = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if not notif.is_read:
        notif.is_read = True
        notif.read_at = timezone.now()
        notif.save(update_fields=["is_read", "read_at"])
    return notifications_panel(request)


@require_POST
@login_required
def notifications_mark_all_read(request):
    Notification.objects.filter(recipient=request.user, is_read=False).update(
        is_read=True, read_at=timezone.now()
    )
    return notifications_panel(request)


# ─── My Activity panel ──────────────────────────────────────────

@login_required
def my_activity(request):
    """HTMX-loaded 'My Activity' partial. Returns the user's last 20
    interactions today: writes (CREATE/UPDATE/DELETE) on clinical/financial
    entities, plus key reads (patient chart views).

    Skips noise (request audits from background polls, every GET, etc.) so
    the panel highlights *interactions* — actual actions the user took.
    """
    from .models import AuditLog
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Writes always count. Reads only count for patient chart views — those
    # are clinically meaningful (a user looked at a chart) versus, say,
    # opening the appointments queue.
    qs = (
        AuditLog.objects.filter(actor=request.user, timestamp__gte=today_start)
        .filter(
            # Writes on anything — those are interactions.
            # OR patient-chart reads (entity_type="Patient", action="READ").
            # We can't easily express "OR" in a single .filter, so split:
        )
    )
    # Just pull the user's actions for today, filter Python-side; volumes
    # are small enough that this is fine.
    actions = list(
        AuditLog.objects.filter(
            actor=request.user, timestamp__gte=today_start,
        ).order_by("-timestamp")[:80]
    )

    # Keep meaningful events: any write, or a Patient READ (chart access).
    meaningful = []
    for a in actions:
        if a.action in ("CREATE", "UPDATE", "DELETE"):
            meaningful.append(a)
        elif a.action == "READ" and a.entity_type == "Patient":
            meaningful.append(a)
        if len(meaningful) >= 20:
            break

    return render(request, "core/_my_activity.html", {
        "activity": meaningful,
        "today_start": today_start,
    })
