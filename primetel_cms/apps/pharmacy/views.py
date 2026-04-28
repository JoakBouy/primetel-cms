"""Primetel CMS — Pharmacy Views."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.decorators import requires_role
from apps.core.pdf import render_pdf
from apps.encounters.models import Encounter

from .allergy import check_allergy
from .models import Dispense, Drug, Prescription, StockItem


@requires_role("PHARMACY", "ADMIN")
def rx_queue(request):
    """Prescription queue — shows all PRESCRIBED items awaiting dispensing."""
    pending = Prescription.objects.filter(
        status__in=["PRESCRIBED", "PARTIALLY_DISPENSED"]
    ).select_related("encounter__patient", "drug").order_by("prescribed_at")
    return render(request, "pharmacy/rx_queue.html", {
        "page_title": _("Rx Queue"),
        "prescriptions": pending,
    })


@requires_role("PHARMACY", "ADMIN")
def stock_list(request):
    """Drug stock overview."""
    drugs = Drug.objects.filter(is_active=True).prefetch_related("stock_items")
    return render(request, "pharmacy/stock.html", {
        "page_title": _("Stock Management"),
        "drugs": drugs,
    })


@requires_role("PHARMACY", "ADMIN")
def drug_detail(request, pk):
    """Single drug stock detail."""
    drug = get_object_or_404(Drug, pk=pk)
    batches = drug.stock_items.all()
    return render(request, "pharmacy/drug_detail.html", {
        "page_title": drug.generic_name,
        "drug": drug,
        "batches": batches,
    })


@requires_role("PHARMACY", "ADMIN")
def rx_dispense(request, pk):
    """Dispense a prescription — picks the earliest-expiring batch with stock by default."""
    rx = get_object_or_404(
        Prescription.objects.select_related("encounter__patient", "drug"), pk=pk
    )
    if rx.status in ("DISPENSED", "CANCELLED"):
        messages.error(request, _("This prescription cannot be dispensed."))
        return redirect("pharmacy:rx_queue")

    available_batches = rx.drug.stock_items.filter(quantity_on_hand__gt=0).order_by("expiry_date")
    already_dispensed = sum(d.quantity_dispensed for d in rx.dispenses.all())
    remaining = max(rx.quantity - already_dispensed, 0)

    if request.method == "POST":
        batch_id = request.POST.get("stock_item")
        try:
            qty = int(request.POST.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            messages.error(request, _("Quantity must be positive."))
            return redirect("pharmacy:rx_dispense", pk=pk)
        if qty > remaining:
            messages.error(request, _("Cannot dispense more than the remaining quantity (%(r)s).") % {"r": remaining})
            return redirect("pharmacy:rx_dispense", pk=pk)
        batch = get_object_or_404(StockItem, pk=batch_id, drug=rx.drug)
        try:
            Dispense(
                prescription=rx,
                stock_item=batch,
                quantity_dispensed=qty,
                dispensed_by=request.user,
                patient_counselled=request.POST.get("counselled") == "on",
            ).save()
            messages.success(request, _("Dispensed %(q)s units.") % {"q": qty})
        except ValidationError as e:
            messages.error(request, e.message if hasattr(e, "message") else str(e))
            return redirect("pharmacy:rx_dispense", pk=pk)
        return redirect("pharmacy:rx_queue")

    return render(request, "pharmacy/dispense.html", {
        "page_title": _("Dispense"),
        "rx": rx,
        "batches": available_batches,
        "remaining": remaining,
    })


@requires_role("CLINICIAN", "ADMIN")
def rx_prescribe(request, encounter_pk):
    """
    Prescribe a drug as part of an encounter.
    Performs a drug-allergy check against the patient's recorded allergies.
    A clinician can override with a documented reason.
    """
    encounter = get_object_or_404(Encounter.objects.for_user(request.user), pk=encounter_pk)
    if encounter.status == "FINALISED":
        messages.error(request, _("Encounter is finalised. Reopen it to add a prescription."))
        return redirect("encounters:detail", pk=encounter.pk)

    drugs = Drug.objects.filter(is_active=True).order_by("generic_name")

    if request.method == "POST":
        drug = get_object_or_404(Drug, pk=request.POST.get("drug"))
        try:
            quantity = int(request.POST.get("quantity") or 0)
            duration_days = int(request.POST.get("duration_days") or 0)
        except (TypeError, ValueError):
            messages.error(request, _("Quantity and duration must be whole numbers."))
            return redirect("pharmacy:rx_prescribe", encounter_pk=encounter.pk)
        dose = (request.POST.get("dose") or "").strip()
        frequency = (request.POST.get("frequency") or "").strip()
        if not dose or not frequency or quantity <= 0 or duration_days <= 0:
            messages.error(request, _("Dose, frequency, quantity and duration are all required."))
            return redirect("pharmacy:rx_prescribe", encounter_pk=encounter.pk)

        # Drug-allergy check
        match = check_allergy(encounter.patient, drug)
        override_ack = request.POST.get("allergy_override") == "on"
        override_reason = (request.POST.get("override_reason") or "").strip()

        if match and not (override_ack and override_reason):
            return render(request, "pharmacy/prescribe.html", {
                "page_title": _("Prescribe"),
                "encounter": encounter,
                "drugs": drugs,
                "allergy_match": match,
                "form_data": request.POST,
            })

        instructions = request.POST.get("instructions", "")
        if match and override_ack:
            instructions = (
                f"[ALLERGY OVERRIDE — {match}] reason: {override_reason}\n{instructions}"
            ).strip()

        Prescription.objects.create(
            encounter=encounter,
            drug=drug,
            dose=dose,
            frequency=frequency,
            duration_days=duration_days,
            quantity=quantity,
            instructions=instructions,
            prescribed_by=request.user,
            created_by=request.user,
        )
        messages.success(request, _("Prescription added."))
        return redirect("encounters:detail", pk=encounter.pk)

    return render(request, "pharmacy/prescribe.html", {
        "page_title": _("Prescribe"),
        "encounter": encounter,
        "drugs": drugs,
    })


@requires_role("CLINICIAN", "PHARMACY", "ADMIN")
def rx_print(request, pk):
    """Render a prescription as a printable PDF (Rx slip)."""
    rx = get_object_or_404(
        Prescription.objects.select_related("encounter__patient", "encounter__clinician", "drug"),
        pk=pk,
    )
    html = render_to_string("pharmacy/rx_print.html", {
        "rx": rx,
        "now": timezone.now(),
    }, request=request)
    return render_pdf(html, filename=f"rx-{rx.pk}.pdf")
