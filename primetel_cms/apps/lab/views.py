"""Primetel CMS — Lab Views."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.decorators import requires_role
from apps.core.pdf import render_pdf
from apps.encounters.models import Encounter

from .models import LabOrder, LabResult, LabTest


@requires_role("LAB", "CLINICIAN", "ADMIN")
def lab_queue(request):
    """Lab queue — tests awaiting processing."""
    pending = LabOrder.objects.filter(
        status__in=["ORDERED", "COLLECTED", "RESULTED"]
    ).select_related("encounter__patient", "test").order_by("ordered_at")
    return render(request, "lab/queue.html", {
        "page_title": _("Lab Queue"),
        "orders": pending,
    })


@requires_role("LAB", "CLINICIAN", "ADMIN")
def lab_order_detail(request, pk):
    """Lab order detail / result entry."""
    order = get_object_or_404(
        LabOrder.objects.select_related("test", "encounter__patient"), pk=pk
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
    messages.success(request, _("Marked as collected."))
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
    messages.success(request, _("Result saved."))
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


@requires_role("CLINICIAN", "ADMIN")
def lab_order_new(request, encounter_pk):
    """Order a lab test from an encounter."""
    encounter = get_object_or_404(Encounter.objects.for_user(request.user), pk=encounter_pk)
    if encounter.status == "FINALISED":
        messages.error(request, _("Encounter is finalised. Reopen it to order a test."))
        return redirect("encounters:detail", pk=encounter.pk)
    tests = LabTest.objects.filter(is_active=True).order_by("name")
    if request.method == "POST":
        test = get_object_or_404(LabTest, pk=request.POST.get("test"))
        LabOrder.objects.create(
            encounter=encounter, test=test, ordered_by=request.user, created_by=request.user
        )
        messages.success(request, _("Lab order placed."))
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
