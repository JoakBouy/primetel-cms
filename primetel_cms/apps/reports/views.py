"""Reports views."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.db.models import Sum, Count, F, Q
from django.db.models.functions import Coalesce

from apps.encounters.models import Encounter, Diagnosis
from apps.billing.models import Payment
from apps.pharmacy.models import Prescription, Drug, StockItem, Dispense
from apps.lab.models import LabOrder
from apps.core.models import AuditLog

from datetime import timedelta

ROLE_HOME = {
    "RECEPTIONIST": "/appointments/queue/",
    "NURSE": "/appointments/queue/",
    "PHARMACY": "/pharmacy/queue/",
    "LAB": "/lab/queue/",
    "FINANCE": "/billing/invoices/",
}


@login_required
def dashboard(request):
    """Role-scoped dashboard — the main landing page after login.

    Single-purpose roles (LAB, PHARMACY, RECEPTIONIST, NURSE, FINANCE) land
    directly on their work queue. Clinicians, counsellors, and admins see the
    full clinical dashboard.

    Each dashboard card is gated by the same `nav_visible` map that drives the
    top nav. We only compute the metrics the user is allowed to see — both to
    keep the UI honest and to avoid loading data the user shouldn't have.
    """
    role_code = getattr(getattr(request.user, "role", None), "code", None)
    if role_code in ROLE_HOME and not request.user.is_superuser:
        return redirect(ROLE_HOME[role_code])

    # Single source of truth for what this user can see.
    from apps.core.context_processors import _nav_visibility
    nav = _nav_visibility(request.user)

    today = timezone.now().date()
    start_of_day = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
    seven_days_ago = start_of_day - timedelta(days=6)

    context = {"page_title": _("Dashboard")}

    # ── Encounters scope: patients-seen, volume chart, top diagnoses ──
    if nav.get("encounters"):
        user_encounters = Encounter.objects.for_user(request.user)
        context["patients_seen_today"] = user_encounters.filter(started_at__gte=start_of_day).count()

        chart_labels, chart_data = [], []
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()))
            end = start + timedelta(days=1)
            count = user_encounters.filter(started_at__gte=start, started_at__lt=end).count()
            chart_labels.append(day.strftime("%a"))
            chart_data.append(count)
        context["chart_labels"] = chart_labels
        context["chart_data"] = chart_data

        recent_dx = Diagnosis.objects.filter(encounter__in=user_encounters, encounter__started_at__gte=seven_days_ago)
        top_diagnoses = list(
            recent_dx.exclude(icd10_code="")
            .values('icd10_code', 'description')
            .annotate(n=Count('id'))
            .order_by('-n')[:5]
        )
        if len(top_diagnoses) < 5:
            free_text = list(
                recent_dx.filter(icd10_code="")
                .values('description')
                .annotate(n=Count('id'))
                .order_by('-n')[: 5 - len(top_diagnoses)]
            )
            for row in free_text:
                top_diagnoses.append({"icd10_code": "", "description": row["description"], "n": row["n"]})
        context["top_diagnoses"] = top_diagnoses
        context["top_dx_max"] = max((d["n"] for d in top_diagnoses), default=0)

    # ── Billing scope: revenue today ──
    if nav.get("billing"):
        context["revenue_today"] = (
            Payment.objects.filter(received_at__gte=start_of_day).aggregate(Sum('amount_tzs'))['amount_tzs__sum']
            or 0
        )

    # ── Pharmacy scope: dispensed-today, low stock, near expiry ──
    if nav.get("pharmacy"):
        context["rx_dispensed_today"] = (
            Dispense.objects.filter(dispensed_at__gte=start_of_day)
            .values('prescription_id').distinct().count()
        )
        context["low_stock_drugs"] = Drug.objects.filter(is_active=True).annotate(
            total_stock_db=Coalesce(Sum('stock_items__quantity_on_hand'), 0)
        ).filter(total_stock_db__lte=F('low_stock_threshold'))[:5]
        ninety_days_from_now = today + timedelta(days=90)
        context["near_expiry_batches"] = StockItem.objects.filter(
            expiry_date__lte=ninety_days_from_now, quantity_on_hand__gt=0
        ).select_related('drug').order_by('expiry_date')[:5]

    # ── Lab scope: pending labs ──
    if nav.get("lab"):
        context["pending_labs"] = LabOrder.objects.filter(status__in=['ORDERED', 'COLLECTED']).count()

    # ── Admin scope: recent interaction trail ──
    if request.user.is_superuser or role_code == "ADMIN":
        context["interactions_today"] = AuditLog.objects.filter(timestamp__gte=start_of_day).count()
        context["recent_interactions"] = (
            AuditLog.objects.select_related("actor")
            .filter(timestamp__gte=seven_days_ago)
            .order_by("-timestamp")[:12]
        )

    return render(request, "reports/dashboard.html", context)
