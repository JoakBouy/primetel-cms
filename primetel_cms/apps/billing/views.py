"""Primetel CMS — Billing Views."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.decorators import requires_role
from apps.core.pdf import render_pdf
from apps.patients.models import Patient

from .models import Invoice, InvoiceLine, Payment, ServiceItem


@requires_role("RECEPTIONIST", "FINANCE", "ADMIN")
def invoice_list(request):
    """Invoice list — filterable by status."""
    status = request.GET.get("status", "")
    qs = Invoice.objects.select_related("patient").order_by("-issued_at")
    if status:
        qs = qs.filter(status=status)
    return render(request, "billing/invoices.html", {
        "page_title": _("Invoices"),
        "invoices": qs[:100],
        "current_status": status,
    })


@requires_role("RECEPTIONIST", "FINANCE", "ADMIN")
def invoice_detail(request, pk):
    """Invoice detail with lines and payments."""
    invoice = get_object_or_404(Invoice.objects.select_related("patient"), pk=pk)
    return render(request, "billing/invoice_detail.html", {
        "page_title": invoice.invoice_number,
        "invoice": invoice,
        "lines": invoice.lines.all(),
        "payments": invoice.payments.all(),
        "service_items": ServiceItem.objects.filter(is_active=True).order_by("category", "name"),
    })


@requires_role("RECEPTIONIST", "FINANCE", "ADMIN")
def invoice_new(request):
    """Create a new draft invoice for a patient."""
    if request.method == "POST":
        patient = get_object_or_404(Patient, pk=request.POST.get("patient"))
        invoice = Invoice.objects.create(
            patient=patient, issued_by=request.user, created_by=request.user,
        )
        return redirect("billing:invoice_detail", pk=invoice.pk)
    patients = Patient.objects.all()[:200]
    return render(request, "billing/invoice_new.html", {
        "page_title": _("New Invoice"),
        "patients": patients,
    })


@require_POST
@requires_role("RECEPTIONIST", "FINANCE", "ADMIN")
def invoice_add_line(request, pk):
    """Add a line to an invoice."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status not in ("DRAFT", "ISSUED", "PARTIALLY_PAID"):
        messages.error(request, _("This invoice is closed."))
        return redirect("billing:invoice_detail", pk=pk)
    description = (request.POST.get("description") or "").strip()
    try:
        quantity = Decimal(request.POST.get("quantity") or "1")
        unit_price = Decimal(request.POST.get("unit_price_tzs") or "0")
    except InvalidOperation:
        messages.error(request, _("Quantity and price must be numbers."))
        return redirect("billing:invoice_detail", pk=pk)
    if quantity <= 0 or unit_price < 0 or not description:
        messages.error(request, _("Description, positive quantity and non-negative price are required."))
        return redirect("billing:invoice_detail", pk=pk)
    service_item_id = request.POST.get("service_item") or None
    InvoiceLine.objects.create(
        invoice=invoice,
        service_item_id=service_item_id if service_item_id else None,
        description=description,
        quantity=quantity,
        unit_price_tzs=unit_price,
    )
    invoice.recalculate()
    messages.success(request, _("Line added."))
    return redirect("billing:invoice_detail", pk=pk)


@require_POST
@requires_role("RECEPTIONIST", "FINANCE", "ADMIN")
def invoice_issue(request, pk):
    """Move an invoice from DRAFT to ISSUED."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status != "DRAFT":
        messages.error(request, _("Only draft invoices can be issued."))
        return redirect("billing:invoice_detail", pk=pk)
    if not invoice.lines.exists():
        messages.error(request, _("Add at least one line before issuing."))
        return redirect("billing:invoice_detail", pk=pk)
    invoice.recalculate()
    invoice.status = "ISSUED"
    invoice.save(update_fields=["status"])
    messages.success(request, _("Invoice issued."))
    return redirect("billing:invoice_detail", pk=pk)


@require_POST
@requires_role("FINANCE", "RECEPTIONIST", "ADMIN")
def payment_record(request, pk):
    """Record a payment against an invoice."""
    invoice = get_object_or_404(Invoice, pk=pk)
    method = request.POST.get("method", "CASH")
    try:
        amount = Decimal(request.POST.get("amount_tzs") or "0")
    except InvalidOperation:
        messages.error(request, _("Amount must be a number."))
        return redirect("billing:invoice_detail", pk=pk)
    if amount <= 0:
        messages.error(request, _("Payment amount must be positive."))
        return redirect("billing:invoice_detail", pk=pk)
    reference = (request.POST.get("reference") or "").strip()
    waiver_reason_id = request.POST.get("waiver_reason") or None
    try:
        Payment.objects.create(
            invoice=invoice,
            method=method,
            amount_tzs=amount,
            reference=reference,
            waiver_reason_id=waiver_reason_id if waiver_reason_id else None,
            received_by=request.user,
            created_by=request.user,
        )
        messages.success(request, _("Payment recorded."))
    except ValidationError as e:
        messages.error(request, str(e))
    return redirect("billing:invoice_detail", pk=pk)


@requires_role("FINANCE", "RECEPTIONIST", "ADMIN")
def receipt_print(request, pk):
    """Printable receipt for a paid (or partly paid) invoice."""
    invoice = get_object_or_404(
        Invoice.objects.select_related("patient", "issued_by"), pk=pk
    )
    html = render_to_string("billing/receipt_print.html", {
        "invoice": invoice,
        "lines": invoice.lines.all(),
        "payments": invoice.payments.all(),
        "now": timezone.now(),
    }, request=request)
    return render_pdf(html, filename=f"receipt-{invoice.invoice_number}.pdf")
