"""Primetel CMS — Billing Views."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.decorators import requires_role
from apps.core.models import ReasonCode
from apps.core.notifications import notify_role, notify_user
from apps.core.pdf import render_pdf
from apps.core.utils import audit, parse_uuid
from apps.patients.models import Patient

from .models import Invoice, InvoiceLine, Payment, ServiceItem
from .services import refresh_prepay_type

BILLING_ROLES = ("RECEPTIONIST", "FINANCE", "ADMIN")
PAYMENT_METHODS = dict(Payment.METHOD_CHOICES)


def _get_patient_or_404(raw_pk):
    pk = parse_uuid(raw_pk)
    if pk is None:
        raise Http404("Patient not found")
    return get_object_or_404(Patient, pk=pk)


@requires_role(*BILLING_ROLES)
def invoice_list(request):
    """Invoice list — filterable by status and patient, paginated."""
    status = request.GET.get("status", "")
    patient_id = request.GET.get("patient", "")
    qs = Invoice.objects.select_related("patient").order_by("-issued_at")
    if status:
        qs = qs.filter(status=status)
    selected_patient = None
    if patient_id:
        selected_patient = _get_patient_or_404(patient_id)
        qs = qs.filter(patient=selected_patient)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "billing/invoices.html", {
        "page_title": _("Invoices"),
        "invoices": page.object_list,
        "page_obj": page,
        "current_status": status,
        "selected_patient": selected_patient,
    })


@requires_role(*BILLING_ROLES)
def invoice_detail(request, pk):
    """Invoice detail with lines and payments."""
    invoice = get_object_or_404(Invoice.objects.select_related("patient"), pk=pk)
    payments = list(invoice.payments.select_related("received_by", "waiver_reason"))
    voided_ids = {p.reverses_id for p in payments if p.reverses_id}
    for p in payments:
        p.already_voided = p.pk in voided_ids
    return render(request, "billing/invoice_detail.html", {
        "page_title": invoice.invoice_number,
        "invoice": invoice,
        "lines": invoice.lines.all(),
        "payments": payments,
        "service_items": ServiceItem.objects.filter(is_active=True).order_by("category", "name"),
        "waiver_reasons": ReasonCode.objects.filter(category="WAIVER", is_active=True),
        "payment_methods": Payment.METHOD_CHOICES,
    })


@requires_role(*BILLING_ROLES)
def invoice_new(request):
    """Create a new draft invoice for a patient (patient chosen by search)."""
    if request.method == "POST":
        patient = _get_patient_or_404(request.POST.get("patient_id") or request.POST.get("patient"))
        invoice = Invoice.objects.create(
            patient=patient, issued_by=request.user, created_by=request.user,
        )
        return redirect("billing:invoice_detail", pk=invoice.pk)
    selected_patient = None
    selected_patient_id = request.GET.get("patient")
    if selected_patient_id:
        selected_patient = _get_patient_or_404(selected_patient_id)
    return render(request, "billing/invoice_new.html", {
        "page_title": _("New Invoice"),
        "patient": selected_patient,
    })


@require_POST
@requires_role(*BILLING_ROLES)
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
    service_item = None
    raw_service = request.POST.get("service_item")
    if raw_service:
        service_item = ServiceItem.objects.filter(pk=parse_uuid(raw_service)).first() if parse_uuid(raw_service) else None
        if service_item is None:
            messages.error(request, _("Unknown service item."))
            return redirect("billing:invoice_detail", pk=pk)
    InvoiceLine.objects.create(
        invoice=invoice,
        service_item=service_item,
        description=description,
        quantity=quantity,
        unit_price_tzs=unit_price,
    )
    invoice.recalculate()
    refresh_prepay_type(invoice)
    messages.success(request, _("Line added."))
    return redirect("billing:invoice_detail", pk=pk)


@require_POST
@requires_role(*BILLING_ROLES)
def invoice_issue(request, pk):
    """Move an invoice from DRAFT to ISSUED."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status != "DRAFT":
        messages.error(request, _("Only draft invoices can be issued."))
        return redirect("billing:invoice_detail", pk=pk)
    if not invoice.lines.exists():
        messages.error(request, _("Add at least one line before issuing."))
        return redirect("billing:invoice_detail", pk=pk)
    invoice.status = "ISSUED"
    invoice.recalculate()
    messages.success(request, _("Invoice issued."))
    return redirect("billing:invoice_detail", pk=pk)


def _notify_paid(request, invoice):
    """Ping the clinician(s) that a fully paid patient can now be seen."""
    patient_name = invoice.patient.full_name if invoice.patient_id else ""
    if invoice.encounter_id:
        enc = invoice.encounter
        common = {
            "kind": "PAYMENT_RECEIVED",
            "level": "SUCCESS",
            "title": _("Patient paid — ready for consult"),
            "body": f"{patient_name} · {invoice.invoice_number}",
            "url": f"/encounters/{enc.pk}/",
            "entity_type": "Encounter",
            "entity_id": enc.pk,
        }
        if enc.clinician_id:
            notify_user(enc.clinician, **common)
        else:
            notify_role(["CLINICIAN"], exclude_actor=request.user, **common)
    elif invoice.prepay_type:
        # Prepaid consultation — encounter not yet created. Clinicians open
        # the chart and click "Start Consult", which attaches the encounter
        # to this invoice (see consultation.auto_charge).
        roles = ["COUNSELLOR"] if invoice.prepay_type == "MENTAL_HEALTH" else ["CLINICIAN"]
        notify_role(
            roles,
            exclude_actor=request.user,
            kind="PAYMENT_RECEIVED",
            level="SUCCESS",
            title=_("Patient paid — ready for consult"),
            body=f"{patient_name} · {invoice.invoice_number}",
            url=f"/patients/{invoice.patient_id}/" if invoice.patient_id else "/",
            entity_type="Patient",
            entity_id=invoice.patient_id,
        )


@require_POST
@requires_role(*BILLING_ROLES)
def payment_record(request, pk):
    """Record a payment (or a waiver) against an invoice."""
    invoice = get_object_or_404(Invoice, pk=pk)
    method = request.POST.get("method", "CASH")
    if method not in PAYMENT_METHODS:
        messages.error(request, _("Unknown payment method."))
        return redirect("billing:invoice_detail", pk=pk)
    try:
        amount = Decimal(request.POST.get("amount_tzs") or "0")
    except InvalidOperation:
        messages.error(request, _("Amount must be a number."))
        return redirect("billing:invoice_detail", pk=pk)
    if amount <= 0:
        messages.error(request, _("Payment amount must be positive."))
        return redirect("billing:invoice_detail", pk=pk)
    reference = (request.POST.get("reference") or "").strip()[:100]

    waiver_reason = None
    if method == "WAIVER":
        # A waiver writes off money: it needs a reason code and a note, and
        # can't exceed what is still owed.
        reason_pk = parse_uuid(request.POST.get("waiver_reason"))
        waiver_reason = (
            ReasonCode.objects.filter(pk=reason_pk, category="WAIVER", is_active=True).first()
            if reason_pk else None
        )
        if waiver_reason is None or not reference:
            messages.error(request, _("A waiver needs a reason and a note explaining it."))
            return redirect("billing:invoice_detail", pk=pk)
        if amount > invoice.balance_tzs:
            messages.error(request, _("A waiver cannot exceed the outstanding balance."))
            return redirect("billing:invoice_detail", pk=pk)

    try:
        payment = Payment.objects.create(
            invoice=invoice,
            method=method,
            amount_tzs=amount,
            reference=reference,
            waiver_reason=waiver_reason,
            received_by=request.user,
            created_by=request.user,
        )
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect("billing:invoice_detail", pk=pk)

    if method == "WAIVER":
        audit(request, "UPDATE", invoice, change="waiver", payment_id=str(payment.pk),
              amount=str(amount), reason=waiver_reason.code, note=reference)
        messages.success(request, _("Waiver recorded."))
    else:
        messages.success(request, _("Payment recorded."))

    invoice.refresh_from_db()
    if invoice.status == "PAID":
        _notify_paid(request, invoice)
    return redirect("billing:invoice_detail", pk=pk)


@require_POST
@requires_role(*BILLING_ROLES)
def invoice_remove_line(request, pk, line_pk):
    """Remove a line from an open invoice."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status not in ("DRAFT", "ISSUED", "PARTIALLY_PAID"):
        messages.error(request, _("This invoice is closed."))
        return redirect("billing:invoice_detail", pk=pk)
    line = get_object_or_404(InvoiceLine, pk=line_pk, invoice=invoice)
    description = line.description
    line.delete()
    invoice.recalculate()
    refresh_prepay_type(invoice)
    audit(request, "DELETE", invoice, change="remove_invoice_line", line=description)
    if invoice.balance_tzs < 0:
        messages.warning(
            request,
            _("Line removed. The patient has paid %(c)s TZS more than the new total — refund or credit them.")
            % {"c": -invoice.balance_tzs},
        )
    else:
        messages.success(request, _("Line removed."))
    return redirect("billing:invoice_detail", pk=pk)


@require_POST
@requires_role(*BILLING_ROLES)
def payment_void(request, pk, payment_pk):
    """Void a recorded payment by creating a reversing entry. Original stays in history.

    A payment can be voided only once: the original row is locked and checked
    for an existing reversal inside the same transaction, so a double-click or
    resubmitted form can't reverse it twice.
    """
    invoice = get_object_or_404(Invoice, pk=pk)
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, _("A reason is required to void a payment."))
        return redirect("billing:invoice_detail", pk=pk)
    if invoice.is_closed:
        messages.error(request, _("Cannot void payments on a closed invoice."))
        return redirect("billing:invoice_detail", pk=pk)
    try:
        with transaction.atomic():
            payment = get_object_or_404(
                Payment.objects.select_for_update(), pk=payment_pk, invoice=invoice,
            )
            if payment.amount_tzs <= 0 or payment.reverses_id:
                messages.error(request, _("This payment is already a reversal or zero."))
                return redirect("billing:invoice_detail", pk=pk)
            if payment.reversals.exists():
                messages.error(request, _("This payment has already been voided."))
                return redirect("billing:invoice_detail", pk=pk)
            Payment.objects.create(
                invoice=invoice,
                method=payment.method,
                amount_tzs=-payment.amount_tzs,
                reference=f"VOID: {reason}"[:100],
                reverses=payment,
                received_by=request.user,
                created_by=request.user,
            )
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect("billing:invoice_detail", pk=pk)
    audit(request, "UPDATE", invoice, change="void_payment", original_payment_id=str(payment.pk), reason=reason)
    messages.success(request, _("Payment voided. Reversing entry created."))
    return redirect("billing:invoice_detail", pk=pk)


@requires_role(*BILLING_ROLES)
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
