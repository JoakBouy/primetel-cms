"""Primetel CMS — Pharmacy Views."""
from datetime import datetime
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
from apps.encounters.models import Encounter

from .allergy import check_allergy
from .models import Dispense, Drug, Prescription, StockItem, StockMovement


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


# ─── Drug catalogue management (PHARMACY, ADMIN) ──────────────────

DRUG_FORMS = ["TABLET", "CAPSULE", "SYRUP", "INJECTION", "CREAM", "DROPS", "OTHER"]


@requires_role("PHARMACY", "ADMIN")
def drug_catalogue(request):
    """List the full drug formulary, including inactive entries."""
    drugs = Drug.objects.order_by("-is_active", "generic_name")
    return render(request, "pharmacy/catalogue.html", {
        "page_title": _("Drug Formulary"),
        "drugs": drugs,
    })


@requires_role("PHARMACY", "ADMIN")
def drug_create(request):
    """Add a new drug to the formulary."""
    if request.method == "POST":
        try:
            drug = _populate_drug(Drug(), request.POST)
            drug.full_clean()
            drug.save()
            messages.success(request, _("Drug '%(n)s' added.") % {"n": drug.generic_name})
            return redirect("pharmacy:catalogue")
        except Exception as exc:
            messages.error(request, _("Could not save: %(e)s") % {"e": exc})
            return render(request, "pharmacy/drug_form.html", {
                "page_title": _("New Drug"),
                "form_data": request.POST,
                "drug": None,
                "forms": DRUG_FORMS,
            })
    return render(request, "pharmacy/drug_form.html", {
        "page_title": _("New Drug"),
        "form_data": {},
        "drug": None,
        "forms": DRUG_FORMS,
    })


@requires_role("PHARMACY", "ADMIN")
def drug_edit(request, pk):
    """Edit an existing drug."""
    drug = get_object_or_404(Drug, pk=pk)
    if request.method == "POST":
        try:
            _populate_drug(drug, request.POST)
            drug.full_clean()
            drug.save()
            messages.success(request, _("Drug updated."))
            return redirect("pharmacy:drug_detail", pk=drug.pk)
        except Exception as exc:
            messages.error(request, _("Could not save: %(e)s") % {"e": exc})
    return render(request, "pharmacy/drug_form.html", {
        "page_title": _("Edit Drug"),
        "form_data": request.POST or {
            "generic_name": drug.generic_name,
            "brand_name": drug.brand_name,
            "strength": drug.strength,
            "form": drug.form,
            "pack_size": drug.pack_size,
            "unit_price_tzs": drug.unit_price_tzs,
            "low_stock_threshold": drug.low_stock_threshold,
            "is_active": "on" if drug.is_active else "",
        },
        "drug": drug,
        "forms": DRUG_FORMS,
    })


def _populate_drug(drug: Drug, data) -> Drug:
    drug.generic_name = (data.get("generic_name") or "").strip()
    drug.brand_name = (data.get("brand_name") or "").strip()
    drug.strength = (data.get("strength") or "").strip()
    drug.form = data.get("form") or "OTHER"
    drug.is_active = data.get("is_active") == "on"
    for field in ("pack_size", "low_stock_threshold"):
        raw = (data.get(field) or "").strip()
        if not raw:
            setattr(drug, field, 1 if field == "pack_size" else 10)
            continue
        try:
            setattr(drug, field, int(raw))
        except ValueError:
            raise ValueError(f"{field} must be a whole number.")
    raw_price = (data.get("unit_price_tzs") or "").strip()
    try:
        drug.unit_price_tzs = Decimal(raw_price) if raw_price else Decimal(0)
    except InvalidOperation:
        raise ValueError("unit_price_tzs must be a number.")
    if not drug.generic_name or not drug.strength:
        raise ValueError("Generic name and strength are required.")
    if drug.form not in DRUG_FORMS:
        raise ValueError(f"Invalid form '{drug.form}'.")
    return drug


# ─── Stock-receive (new batch) ────────────────────────────────────

@requires_role("PHARMACY", "ADMIN")
def stock_receive(request, drug_pk):
    """Receive a new batch of stock for a drug. Records a StockMovement."""
    drug = get_object_or_404(Drug, pk=drug_pk)
    if request.method == "POST":
        batch_number = (request.POST.get("batch_number") or "").strip()
        expiry_raw = (request.POST.get("expiry_date") or "").strip()
        try:
            qty = int(request.POST.get("quantity_on_hand") or 0)
        except ValueError:
            qty = 0
        if not batch_number or qty <= 0 or not expiry_raw:
            messages.error(request, _("Batch number, positive quantity, and expiry date are all required."))
            return redirect("pharmacy:stock_receive", drug_pk=drug.pk)
        try:
            expiry = datetime.strptime(expiry_raw, "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, _("Expiry date must be YYYY-MM-DD."))
            return redirect("pharmacy:stock_receive", drug_pk=drug.pk)
        if expiry <= timezone.now().date():
            messages.error(request, _("Expiry date must be in the future."))
            return redirect("pharmacy:stock_receive", drug_pk=drug.pk)
        item = StockItem.objects.create(
            drug=drug, batch_number=batch_number, expiry_date=expiry, quantity_on_hand=qty,
        )
        StockMovement.objects.create(
            stock_item=item, movement_type="RECEIVE", quantity=qty, performed_by=request.user,
        )
        messages.success(request, _("Received %(q)s units (batch %(b)s).") % {"q": qty, "b": batch_number})
        return redirect("pharmacy:drug_detail", pk=drug.pk)
    return render(request, "pharmacy/stock_receive.html", {
        "page_title": _("Receive stock"),
        "drug": drug,
    })


# ─── Drug toggle-active (soft delete / restore) ──────────────────

@require_POST
@requires_role("PHARMACY", "ADMIN")
def drug_toggle_active(request, pk):
    """Toggle a drug's active status (soft delete / restore)."""
    drug = get_object_or_404(Drug, pk=pk)
    drug.is_active = not drug.is_active
    drug.save(update_fields=["is_active"])
    if drug.is_active:
        messages.success(request, _("Drug '%(n)s' reactivated.") % {"n": drug.generic_name})
    else:
        messages.success(request, _("Drug '%(n)s' deactivated.") % {"n": drug.generic_name})
    return redirect("pharmacy:catalogue")


# ─── Stock adjustment ─────────────────────────────────────────────

@require_POST
@requires_role("PHARMACY", "ADMIN")
def stock_adjust(request, item_pk):
    """Adjust stock quantity on an existing batch (e.g. breakage, count correction)."""
    item = get_object_or_404(StockItem.objects.select_related("drug"), pk=item_pk)
    try:
        new_qty = int(request.POST.get("new_quantity") or 0)
    except (TypeError, ValueError):
        new_qty = 0
    if new_qty < 0:
        messages.error(request, _("Stock quantity cannot be negative."))
        return redirect("pharmacy:drug_detail", pk=item.drug.pk)
    reason_text = (request.POST.get("reason") or "").strip()
    if not reason_text:
        messages.error(request, _("A reason is required for stock adjustments."))
        return redirect("pharmacy:drug_detail", pk=item.drug.pk)

    diff = new_qty - item.quantity_on_hand
    item.quantity_on_hand = new_qty
    item.save(update_fields=["quantity_on_hand"])
    StockMovement.objects.create(
        stock_item=item, movement_type="ADJUST", quantity=diff,
        performed_by=request.user,
    )
    messages.success(request, _("Stock adjusted by %(d)s units. Reason: %(r)s") % {"d": diff, "r": reason_text})
    return redirect("pharmacy:drug_detail", pk=item.drug.pk)
