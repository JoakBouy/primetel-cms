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
from apps.core.models import AuditLog
from apps.core.notifications import notify_role, notify_user
from apps.core.pdf import render_pdf
from apps.patients.models import Patient

from .models import Invoice, InvoiceLine, Payment, ServiceItem


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


@requires_role("RECEPTIONIST", "FINANCE", "ADMIN")
def invoice_list(request):
    """Invoice list — filterable by status."""
    status = request.GET.get("status", "")
    patient_id = request.GET.get("patient", "")
    qs = Invoice.objects.select_related("patient").order_by("-issued_at")
    if status:
        qs = qs.filter(status=status)
    selected_patient = None
    if patient_id:
        selected_patient = get_object_or_404(Patient, pk=patient_id)
        qs = qs.filter(patient=selected_patient)
    return render(request, "billing/invoices.html", {
        "page_title": _("Invoices"),
        "invoices": qs[:100],
        "current_status": status,
        "selected_patient": selected_patient,
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
    selected_patient = None
    selected_patient_id = request.GET.get("patient")
    if selected_patient_id:
        selected_patient = get_object_or_404(Patient, pk=selected_patient_id)
    patients = Patient.objects.all()[:200]
    return render(request, "billing/invoice_new.html", {
        "page_title": _("New Invoice"),
        "patients": patients,
        "selected_patient": selected_patient,
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
        # Refresh status from DB after Payment.save() updates it.
        invoice.refresh_from_db()
        # If the payment fully clears a consultation invoice, ping the clinician
        # so they know the patient can be seen now.
        if invoice.encounter_id and invoice.status == "PAID":
            enc = invoice.encounter
            url = f"/encounters/{enc.pk}/"
            patient_name = enc.patient.full_name if enc.patient_id else ""
            if enc.clinician_id:
                notify_user(
                    enc.clinician,
                    kind="PAYMENT_RECEIVED",
                    level="SUCCESS",
                    title=_("Patient paid — ready for consult"),
                    body=f"{patient_name} · {invoice.invoice_number}",
                    url=url,
                    entity_type="Encounter",
                    entity_id=enc.pk,
                )
            else:
                # Fan out to all clinicians on duty if no specific clinician assigned.
                notify_role(
                    ["CLINICIAN", "NURSE"],
                    exclude_actor=request.user,
                    kind="PAYMENT_RECEIVED",
                    level="SUCCESS",
                    title=_("Patient paid — ready for consult"),
                    body=f"{patient_name} · {invoice.invoice_number}",
                    url=url,
                    entity_type="Encounter",
                    entity_id=enc.pk,
                )
    except ValidationError as e:
        messages.error(request, str(e))
    return redirect("billing:invoice_detail", pk=pk)


@require_POST
@requires_role("FINANCE", "RECEPTIONIST", "ADMIN")
def invoice_remove_line(request, pk, line_pk):
    """Remove a line from an open invoice (DRAFT/ISSUED/PARTIALLY_PAID)."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status not in ("DRAFT", "ISSUED", "PARTIALLY_PAID"):
        messages.error(request, _("This invoice is closed."))
        return redirect("billing:invoice_detail", pk=pk)
    line = get_object_or_404(InvoiceLine, pk=line_pk, invoice=invoice)
    description = line.description
    line.delete()
    invoice.recalculate()
    _audit(request, "DELETE", invoice, change="remove_invoice_line", line=description)
    messages.success(request, _("Line removed."))
    return redirect("billing:invoice_detail", pk=pk)


@require_POST
@requires_role("FINANCE", "RECEPTIONIST", "ADMIN")
def payment_void(request, pk, payment_pk):
    """Void a recorded payment by creating a reversing entry. Original stays in history."""
    invoice = get_object_or_404(Invoice, pk=pk)
    payment = get_object_or_404(Payment, pk=payment_pk, invoice=invoice)
    if payment.amount_tzs <= 0:
        messages.error(request, _("This payment is already a reversal or zero."))
        return redirect("billing:invoice_detail", pk=pk)
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, _("A reason is required to void a payment."))
        return redirect("billing:invoice_detail", pk=pk)
    if invoice.status in ("CANCELLED", "WAIVED"):
        messages.error(request, _("Cannot void payments on a closed invoice."))
        return redirect("billing:invoice_detail", pk=pk)
    try:
        # Reopen the invoice so the reversing Payment.save() doesn't reject it.
        if invoice.status == "PAID":
            invoice.status = "PARTIALLY_PAID"
            invoice.save(update_fields=["status"])
        Payment.objects.create(
            invoice=invoice,
            method=payment.method,
            amount_tzs=-payment.amount_tzs,
            reference=f"VOID of {payment.pk}: {reason}"[:100],
            received_by=request.user,
            created_by=request.user,
        )
        _audit(request, "UPDATE", invoice, change="void_payment", original_payment_id=str(payment.pk), reason=reason)
        messages.success(request, _("Payment voided. Reversing entry created."))
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
