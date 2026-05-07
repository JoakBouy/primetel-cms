"""Primetel CMS — Lab Views."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, F, ExpressionWrapper, DurationField
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.decorators import requires_role
from apps.core.models import AuditLog
from apps.core.notifications import notify_role, notify_user
from apps.core.pdf import render_pdf
from apps.encounters.models import Encounter

from .models import LabOrder, LabResult, LabTest


def _audit(request, action, obj, **metadata):
    """Record an AuditLog entry for amendment-style changes."""
    try:
        AuditLog.objects.create(
            actor=request.user,
            action=action,
            entity_type=obj.__class__.__name__,
            entity_id=getattr(obj, "pk", None),
            metadata=metadata,
            ip_address=(request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("REMOTE_ADDR") or "").split(",")[0].strip() or None,
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        )
    except Exception:
        pass


@requires_role("LAB", "CLINICIAN", "PHARMACY", "ADMIN")
@never_cache
def lab_queue(request):
    """Lab queue — tests awaiting processing (not yet resulted)."""
    pending = LabOrder.objects.filter(
        status__in=["ORDERED", "COLLECTED"]
    ).select_related("encounter__patient", "test", "ordered_by").order_by("ordered_at")
    return render(request, "lab/queue.html", {
        "page_title": _("Lab Queue"),
        "orders": pending,
    })


@requires_role("LAB", "CLINICIAN", "PHARMACY", "ADMIN")
@never_cache
def lab_results(request):
    """Processed lab tests — results entered (RESULTED) and clinician-reviewed (REVIEWED)."""
    orders = LabOrder.objects.filter(
        status__in=["RESULTED", "REVIEWED"]
    ).select_related("encounter__patient", "test", "result", "result__performed_by").order_by("-resulted_at", "-ordered_at")
    return render(request, "lab/results.html", {
        "page_title": _("Lab Results"),
        "orders": orders,
    })


@requires_role("LAB", "CLINICIAN", "PHARMACY", "ADMIN")
@never_cache
def lab_order_detail(request, pk):
    """Lab order detail / result entry."""
    order = get_object_or_404(
        LabOrder.objects.select_related("test", "encounter__patient", "ordered_by"), pk=pk
    )
    result = getattr(order, "result", None)
    flag = _compute_flag(order, result.value_numeric) if result and result.value_numeric is not None else None
    return render(request, "lab/order_detail.html", {
        "page_title": order.test.name,
        "order": order,
        "result": result,
        "computed_flag": flag,
    })


@require_POST
@requires_role("LAB", "ADMIN")
def lab_collect(request, pk):
    """Mark a lab order as 'sample collected'."""
    order = get_object_or_404(LabOrder, pk=pk)
    if order.status != "ORDERED":
        messages.error(request, _("Only newly ordered tests can be marked collected."))
        return redirect("lab:order_detail", pk=pk)
    order.status = "COLLECTED"
    order.collected_at = timezone.now()
    order.updated_by = request.user
    order.save(update_fields=["status", "collected_at", "updated_by", "updated_at"])
    messages.success(request, _("Sample collected. You can now enter results."))
    return redirect("lab:order_detail", pk=pk)


@require_POST
@requires_role("LAB", "ADMIN")
def lab_result_enter(request, pk):
    """Enter or update a lab result. Sets status to RESULTED."""
    order = get_object_or_404(LabOrder.objects.select_related("test"), pk=pk)
    if order.status in ("CANCELLED", "REVIEWED"):
        messages.error(request, _("This order is closed."))
        return redirect("lab:order_detail", pk=pk)

    value_numeric = request.POST.get("value_numeric") or ""
    value_text = (request.POST.get("value_text") or "").strip()
    notes = (request.POST.get("notes") or "").strip()

    parsed_numeric = None
    if value_numeric:
        try:
            parsed_numeric = Decimal(value_numeric)
        except (InvalidOperation, TypeError):
            messages.error(request, _("Numeric value is not a valid number."))
            return redirect("lab:order_detail", pk=pk)

    if parsed_numeric is None and not value_text:
        messages.error(request, _("Provide a numeric value or text result."))
        return redirect("lab:order_detail", pk=pk)

    flag = _compute_flag(order, parsed_numeric)

    with transaction.atomic():
        result, _created = LabResult.objects.update_or_create(
            lab_order=order,
            defaults={
                "value_numeric": parsed_numeric,
                "value_text": value_text,
                "flag": flag,
                "performed_by": request.user,
                "notes": notes,
                "created_by": request.user,
            },
        )
        order.status = "RESULTED"
        order.resulted_at = timezone.now()
        order.updated_by = request.user
        order.save(update_fields=["status", "resulted_at", "updated_by", "updated_at"])
    messages.success(request, _("Result saved and visible to the clinician."))

    # Notify the ordering clinician. Critical flags bump to CRITICAL level so the
    # bell shows red and they don't miss it.
    is_critical = (flag == "CRITICAL")
    notify_user(
        order.ordered_by,
        kind="LAB_CRITICAL" if is_critical else "LAB_RESULT_READY",
        level="CRITICAL" if is_critical else "INFO",
        title=(_("CRITICAL lab result") if is_critical else _("Lab result ready")),
        body=f"{order.encounter.patient.full_name} · {order.test.code} {order.test.name}"
             + (f" · {parsed_numeric}" if parsed_numeric is not None else (f" · {value_text[:40]}" if value_text else "")),
        url=f"/lab/orders/{order.pk}/",
        entity_type="LabOrder",
        entity_id=order.pk,
    )
    return redirect("lab:order_detail", pk=pk)


@require_POST
@requires_role("CLINICIAN", "ADMIN")
def lab_review(request, pk):
    """Mark a result as reviewed by the ordering clinician."""
    order = get_object_or_404(LabOrder, pk=pk)
    if order.status != "RESULTED":
        messages.error(request, _("Only resulted orders can be reviewed."))
        return redirect("lab:order_detail", pk=pk)
    order.status = "REVIEWED"
    order.reviewed_at = timezone.now()
    order.updated_by = request.user
    order.save(update_fields=["status", "reviewed_at", "updated_by", "updated_at"])
    messages.success(request, _("Marked as reviewed."))
    return redirect("lab:order_detail", pk=pk)


@require_POST
@requires_role("LAB", "CLINICIAN", "ADMIN")
def lab_amend_result(request, pk):
    """Reopen a REVIEWED lab order so its result can be corrected. Requires a reason."""
    order = get_object_or_404(LabOrder, pk=pk)
    if order.status != "REVIEWED":
        messages.error(request, _("Only reviewed results can be amended. Edit the result directly otherwise."))
        return redirect("lab:order_detail", pk=pk)
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, _("A reason is required to amend a reviewed result."))
        return redirect("lab:order_detail", pk=pk)
    order.status = "RESULTED"
    order.reviewed_at = None
    order.updated_by = request.user
    order.save(update_fields=["status", "reviewed_at", "updated_by", "updated_at"])
    _audit(request, "UPDATE", order, change="amend_reviewed_result", reason=reason)
    messages.success(request, _("Result reopened for correction. Reason recorded; previous values retained in history."))
    return redirect("lab:order_detail", pk=pk)


@require_POST
@requires_role("CLINICIAN", "LAB", "ADMIN")
def lab_cancel(request, pk):
    """Cancel a mistakenly-ordered test. Only allowed before result entry."""
    order = get_object_or_404(LabOrder, pk=pk)
    if order.status not in ("ORDERED", "COLLECTED"):
        messages.error(request, _("Only un-resulted orders can be cancelled. Resulted orders must be amended."))
        return redirect("lab:order_detail", pk=pk)
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, _("A reason is required to cancel an order."))
        return redirect("lab:order_detail", pk=pk)
    order.status = "CANCELLED"
    order.external_reference = (
        (order.external_reference + " | " if order.external_reference else "")
        + f"CANCELLED by {request.user}: {reason}"
    )[:100]
    order.updated_by = request.user
    order.save(update_fields=["status", "external_reference", "updated_by", "updated_at"])
    messages.success(request, _("Order cancelled."))
    return redirect("lab:order_detail", pk=pk)


@requires_role("CLINICIAN", "ADMIN")
def lab_order_new(request, encounter_pk):
    """Order a lab test from an encounter."""
    encounter = get_object_or_404(Encounter.objects.for_user(request.user), pk=encounter_pk)
    if encounter.status == "FINALISED":
        messages.error(request, _("Encounter is finalised. Reopen it to order a test."))
        return redirect("encounters:detail", pk=encounter.pk)
    # Hard payment gate: services rendered only after billing.
    from apps.billing.models import Invoice
    inv = Invoice.objects.filter(encounter=encounter).first()
    if inv is not None and inv.status not in ("PAID", "WAIVED") and inv.balance_tzs > 0:
        messages.error(request, _("Awaiting payment confirmation. The receptionist must record the consultation payment before lab tests can be ordered."))
        return redirect("encounters:detail", pk=encounter.pk)
    tests = LabTest.objects.filter(is_active=True).order_by("name")
    if request.method == "POST":
        test = get_object_or_404(LabTest, pk=request.POST.get("test"))
        order = LabOrder.objects.create(
            encounter=encounter, test=test, ordered_by=request.user, created_by=request.user
        )
        messages.success(request, _("Lab order placed."))
        notify_role(
            "LAB",
            exclude_actor=request.user,
            kind="LAB_RESULT_READY",  # repurposed: "lab work to do"
            level="INFO",
            title=_("New lab order"),
            body=f"{encounter.patient.full_name} · {test.code} {test.name}",
            url=f"/lab/orders/{order.pk}/",
            entity_type="LabOrder",
            entity_id=order.pk,
        )
        return redirect("encounters:detail", pk=encounter.pk)
    return render(request, "lab/order_new.html", {
        "page_title": _("Order Lab Test"),
        "encounter": encounter,
        "tests": tests,
    })


@requires_role("LAB", "CLINICIAN", "ADMIN")
def lab_order_print(request, pk):
    """Printable lab requisition / report."""
    order = get_object_or_404(
        LabOrder.objects.select_related("test", "encounter__patient", "ordered_by"), pk=pk
    )
    html = render_to_string("lab/order_print.html", {
        "order": order,
        "result": getattr(order, "result", None),
        "now": timezone.now(),
    }, request=request)
    return render_pdf(html, filename=f"lab-{order.pk}.pdf")


@requires_role("LAB", "ADMIN")
@never_cache
def lab_test_catalogue(request):
    """List the lab test catalogue. LAB techs can add and edit entries."""
    tests = LabTest.objects.order_by("-is_active", "name")
    return render(request, "lab/catalogue.html", {
        "page_title": _("Lab Test Catalogue"),
        "tests": tests,
    })


@requires_role("LAB", "ADMIN")
@never_cache
def lab_test_create(request):
    """Add a new lab test to the catalogue."""
    if request.method == "POST":
        try:
            test = _populate_test(LabTest(), request.POST)
            test.full_clean()
            test.save()
            messages.success(request, _("Lab test '%(n)s' added.") % {"n": test.name})
            return redirect("lab:catalogue")
        except Exception as exc:
            messages.error(request, _("Could not save: %(e)s") % {"e": exc})
            return render(request, "lab/test_form.html", {
                "page_title": _("New Lab Test"),
                "form_data": request.POST,
                "test": None,
            })
    return render(request, "lab/test_form.html", {
        "page_title": _("New Lab Test"),
        "form_data": {},
        "test": None,
    })


@requires_role("LAB", "ADMIN")
@never_cache
def lab_test_edit(request, pk):
    """Edit an existing lab test in the catalogue."""
    test = get_object_or_404(LabTest, pk=pk)
    if request.method == "POST":
        try:
            _populate_test(test, request.POST)
            test.full_clean()
            test.save()
            messages.success(request, _("Lab test updated."))
            return redirect("lab:catalogue")
        except Exception as exc:
            messages.error(request, _("Could not save: %(e)s") % {"e": exc})
    return render(request, "lab/test_form.html", {
        "page_title": _("Edit Lab Test"),
        "form_data": request.POST or {
            "code": test.code, "name": test.name,
            "specimen_type": test.specimen_type,
            "reference_range_min": test.reference_range_min or "",
            "reference_range_max": test.reference_range_max or "",
            "reference_unit": test.reference_unit,
            "price_tzs": test.price_tzs,
            "is_active": "on" if test.is_active else "",
            "is_send_out": "on" if test.is_send_out else "",
        },
        "test": test,
    })


@require_POST
@requires_role("LAB", "ADMIN")
def lab_test_toggle_active(request, pk):
    """Toggle a lab test's active status (soft delete / restore)."""
    test = get_object_or_404(LabTest, pk=pk)
    test.is_active = not test.is_active
    test.save(update_fields=["is_active"])
    if test.is_active:
        messages.success(request, _("Lab test '%(n)s' reactivated.") % {"n": test.name})
    else:
        messages.success(request, _("Lab test '%(n)s' deactivated.") % {"n": test.name})
    return redirect("lab:catalogue")


def _populate_test(test: LabTest, data) -> LabTest:
    """Apply form data to a LabTest instance, parsing decimals carefully."""
    test.code = (data.get("code") or "").strip()
    test.name = (data.get("name") or "").strip()
    test.specimen_type = data.get("specimen_type") or "OTHER"
    test.reference_unit = (data.get("reference_unit") or "").strip()
    test.is_active = data.get("is_active") == "on"
    test.is_send_out = data.get("is_send_out") == "on"
    for field in ("reference_range_min", "reference_range_max", "price_tzs"):
        raw = (data.get(field) or "").strip()
        if raw == "" or raw is None:
            setattr(test, field, None if field != "price_tzs" else Decimal(0))
            continue
        try:
            setattr(test, field, Decimal(raw))
        except InvalidOperation:
            raise ValueError(f"{field} must be a number.")
    if not test.code or not test.name:
        raise ValueError("Code and name are required.")
    return test


def _compute_flag(order: LabOrder, value):
    """Auto-flag a numeric result against the test's reference range."""
    if value is None:
        return None
    test = order.test
    lo = test.reference_range_min
    hi = test.reference_range_max
    if lo is not None and value < lo:
        # Critical if more than 25% below the lower bound
        if lo > 0 and value < lo * Decimal("0.75"):
            return "CRITICAL"
        return "LOW"
    if hi is not None and value > hi:
        if value > hi * Decimal("1.5"):
            return "CRITICAL"
        return "HIGH"
    return "NORMAL"


# ──────────────────────────────────────────────────────────────────
#  Lab reports (volume, top tests, flags, TAT) — weekly/biweekly/monthly
# ──────────────────────────────────────────────────────────────────

# Period key → number of days in that window. Weekly = 7, biweekly = 14,
# monthly = 30 (calendar month is fine but a 30-day rolling window is simpler
# and matches "monthly" as people usually mean it for ops dashboards).
_REPORT_PERIODS = [
    ("week", _("This week"), 7),
    ("biweek", _("Last 2 weeks"), 14),
    ("month", _("Last 30 days"), 30),
]


def _median(values):
    """Pure-Python median; returns None for empty input. Avoids numpy dep."""
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2


@requires_role("LAB", "CLINICIAN", "ADMIN")
@never_cache
def lab_reports(request):
    """Lab operational reports for a configurable rolling window.

    Query: ?period=week|biweek|month (default: week).
    Returns volume by status, top requested tests, abnormal-flag counts,
    and median turnaround time (ORDERED → RESULTED).
    """
    selected = request.GET.get("period", "week")
    period_lookup = {key: (label, days) for key, label, days in _REPORT_PERIODS}
    if selected not in period_lookup:
        selected = "week"
    period_label, period_days = period_lookup[selected]
    now = timezone.now()
    since = now - timedelta(days=period_days)

    in_window = LabOrder.objects.filter(ordered_at__gte=since)

    # Volume by terminal status reached in the window. We bucket by current
    # status so a result entered today shows up in RESULTED even if the order
    # was placed at the start of the window. CANCELLED is shown separately.
    counts_qs = in_window.values("status").annotate(n=Count("id"))
    volume = {"ORDERED": 0, "COLLECTED": 0, "RESULTED": 0, "REVIEWED": 0, "CANCELLED": 0}
    for row in counts_qs:
        if row["status"] in volume:
            volume[row["status"]] = row["n"]
    volume_total = sum(volume.values())

    # Top tests by request volume.
    top_tests = list(
        in_window.values("test__code", "test__name")
        .annotate(n=Count("id"))
        .order_by("-n")[:10]
    )
    top_tests_max = max((row["n"] for row in top_tests), default=0)

    # Abnormal-flag distribution. Only count results entered in the window so
    # the "this week" number reflects actual lab work this week.
    flag_qs = (
        LabResult.objects.filter(created_at__gte=since)
        .values("flag")
        .annotate(n=Count("id"))
    )
    flags = {"NORMAL": 0, "LOW": 0, "HIGH": 0, "CRITICAL": 0, "UNFLAGGED": 0}
    for row in flag_qs:
        key = row["flag"] or "UNFLAGGED"
        if key in flags:
            flags[key] = row["n"]
    flags_total = sum(flags.values())

    # Median turnaround time (hours) from order to result. Ignore unresulted
    # orders. Compute in Python — pulling timestamps and median-ing a few
    # hundred rows is cheaper than a percentile aggregate that varies by DB.
    tat_seconds = []
    for o in in_window.exclude(resulted_at__isnull=True).only("ordered_at", "resulted_at"):
        delta = (o.resulted_at - o.ordered_at).total_seconds()
        if delta >= 0:
            tat_seconds.append(delta)
    tat_median_seconds = _median(tat_seconds)
    tat_median_hours = round(tat_median_seconds / 3600.0, 1) if tat_median_seconds is not None else None
    tat_count = len(tat_seconds)

    return render(request, "lab/reports.html", {
        "page_title": _("Lab Reports"),
        "periods": _REPORT_PERIODS,
        "selected_period": selected,
        "period_label": period_label,
        "period_days": period_days,
        "since": since,
        "volume": volume,
        "volume_total": volume_total,
        "top_tests": top_tests,
        "top_tests_max": top_tests_max,
        "flags": flags,
        "flags_total": flags_total,
        "tat_median_hours": tat_median_hours,
        "tat_count": tat_count,
    })
