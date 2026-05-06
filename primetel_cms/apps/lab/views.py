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


@requires_role("LAB", "ADMIN")
def lab_test_catalogue(request):
    """List the lab test catalogue. LAB techs can add and edit entries."""
    tests = LabTest.objects.order_by("-is_active", "name")
    return render(request, "lab/catalogue.html", {
        "page_title": _("Lab Test Catalogue"),
        "tests": tests,
    })


@requires_role("LAB", "ADMIN")
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
