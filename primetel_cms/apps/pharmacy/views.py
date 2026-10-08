"""Primetel CMS — Pharmacy Views."""
import calendar
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.decorators import requires_role
from apps.billing.services import (
    bill_prescription, remove_prescription_charge, update_prescription_charge,
)
from apps.core.models import ReasonCode
from apps.core.notifications import notify_role, notify_user
from apps.core.pdf import render_pdf
from apps.core.utils import audit, parse_uuid
from apps.encounters.models import Encounter

from .allergy import check_allergy
from .models import Dispense, Drug, Prescription, StockItem, StockMovement


def _prescriptions_for_user(user):
    """Pharmacy (and admins) work every prescription; clinicians only see
    prescriptions on encounters they're allowed to open (mental-health rules)."""
    if user.has_role("PHARMACY", "ADMIN"):
        return Prescription.objects.all()
    return Prescription.objects.filter(encounter__in=Encounter.objects.for_user(user))


def _charge_removed_message(request, invoice):
    if invoice is None:
        return
    if invoice.balance_tzs < 0:
        messages.warning(
            request,
            _("Charge removed from invoice %(n)s. The patient has paid %(c)s TZS more than they owe — refund or credit them.")
            % {"n": invoice.invoice_number, "c": -invoice.balance_tzs},
        )
    else:
        messages.info(request, _("Charge removed from invoice %(n)s.") % {"n": invoice.invoice_number})


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
    movements = (
        StockMovement.objects.filter(stock_item__drug=drug)
        .select_related("stock_item", "performed_by", "reason")
        .order_by("-performed_at")[:30]
    )
    return render(request, "pharmacy/drug_detail.html", {
        "page_title": drug.generic_name,
        "drug": drug,
        "batches": batches,
        "movements": movements,
        "stock_reasons": ReasonCode.objects.filter(category="STOCK_ADJUSTMENT", is_active=True),
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

    # Hard payment gate: don't dispense until the bill is settled. Pharmacy
    # staff should send the patient back to the till instead of dispensing
    # against an unpaid balance.
    try:
        from apps.billing.models import Invoice
        invoice = Invoice.objects.filter(encounter_id=rx.encounter_id).first()
        if invoice is not None and invoice.status not in ("PAID", "WAIVED") and invoice.balance_tzs > 0:
            messages.error(
                request,
                _("Awaiting payment confirmation. Patient owes %(bal)s TZS — send them to the front desk before dispensing.")
                % {"bal": invoice.balance_tzs},
            )
            return redirect("pharmacy:rx_queue")
    except Exception:
        # If billing lookup fails for any reason, don't block dispensing.
        invoice = None

    today = timezone.localdate()
    # Expired batches are never offered (and the model refuses them anyway).
    available_batches = rx.drug.stock_items.filter(
        quantity_on_hand__gt=0, expiry_date__gte=today,
    ).order_by("expiry_date")
    expired_on_hand = rx.drug.stock_items.filter(quantity_on_hand__gt=0, expiry_date__lt=today)
    already_dispensed = sum(d.quantity_dispensed for d in rx.dispenses.all())
    remaining = max(rx.quantity - already_dispensed, 0)

    if request.method == "POST":
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
        batch_id = parse_uuid(request.POST.get("stock_item"))
        if batch_id is None:
            messages.error(request, _("Select a batch to dispense from."))
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
            rx.refresh_from_db()
            # Tell the prescribing clinician that the patient has been served.
            if rx.prescribed_by_id:
                notify_user(
                    rx.prescribed_by,
                    kind="RX_DISPENSED",
                    level="SUCCESS" if rx.status == "DISPENSED" else "INFO",
                    title=(_("Prescription dispensed") if rx.status == "DISPENSED" else _("Prescription partially dispensed")),
                    body=f"{rx.encounter.patient.full_name} · {rx.drug.generic_name} {rx.drug.strength}",
                    url=f"/encounters/{rx.encounter_id}/",
                    entity_type="Prescription",
                    entity_id=rx.pk,
                )
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirect("pharmacy:rx_dispense", pk=pk)
        return redirect("pharmacy:rx_queue")

    return render(request, "pharmacy/dispense.html", {
        "page_title": _("Dispense"),
        "rx": rx,
        "batches": available_batches,
        "expired_batches": expired_on_hand,
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
    # Hard payment gate: prescriptions written only after consultation paid.
    from apps.billing.models import Invoice
    inv = Invoice.objects.filter(encounter=encounter).first()
    if inv is not None and inv.status not in ("PAID", "WAIVED") and inv.balance_tzs > 0:
        messages.error(request, _("Awaiting payment confirmation. The receptionist must record the consultation payment before prescriptions can be written."))
        return redirect("encounters:detail", pk=encounter.pk)

    drugs = Drug.objects.filter(is_active=True).order_by("generic_name")

    if request.method == "POST":
        drug_ids = request.POST.getlist("drug") or [request.POST.get("drug")]
        doses = request.POST.getlist("dose") or [request.POST.get("dose")]
        frequencies = request.POST.getlist("frequency") or [request.POST.get("frequency")]
        durations = request.POST.getlist("duration_days") or [request.POST.get("duration_days")]
        quantities = request.POST.getlist("quantity") or [request.POST.get("quantity")]
        instructions_list = request.POST.getlist("instructions") or [request.POST.get("instructions")]
        prescription_rows = []
        max_rows = max(len(drug_ids), len(doses), len(frequencies), len(durations), len(quantities), len(instructions_list))

        try:
            for idx in range(max_rows):
                drug_id = (drug_ids[idx] if idx < len(drug_ids) else "") or ""
                dose = ((doses[idx] if idx < len(doses) else "") or "").strip()
                frequency = ((frequencies[idx] if idx < len(frequencies) else "") or "").strip()
                duration_raw = (durations[idx] if idx < len(durations) else "") or ""
                quantity_raw = (quantities[idx] if idx < len(quantities) else "") or ""
                instructions = (instructions_list[idx] if idx < len(instructions_list) else "") or ""
                if not any([drug_id, dose, frequency, duration_raw, quantity_raw, instructions.strip()]):
                    continue
                drug = Drug.objects.filter(pk=parse_uuid(drug_id), is_active=True).first() if parse_uuid(drug_id) else None
                if drug is None:
                    raise ValueError
                quantity = int(quantity_raw or 0)
                duration_days = int(duration_raw or 0)
                if not dose or not frequency or quantity <= 0 or duration_days <= 0:
                    raise ValueError
                prescription_rows.append({
                    "drug": drug,
                    "dose": dose,
                    "frequency": frequency,
                    "duration_days": duration_days,
                    "quantity": quantity,
                    "instructions": instructions,
                })
        except (TypeError, ValueError):
            messages.error(request, _("Drug, dose, frequency, quantity and duration are required for each medicine."))
            return redirect("pharmacy:rx_prescribe", encounter_pk=encounter.pk)

        if not prescription_rows:
            messages.error(request, _("Add at least one medicine."))
            return redirect("pharmacy:rx_prescribe", encounter_pk=encounter.pk)

        allergy_matches = [
            match for match in (
                check_allergy(encounter.patient, row["drug"]) for row in prescription_rows
            )
            if match
        ]
        override_ack = request.POST.get("allergy_override") == "on"
        override_reason = (request.POST.get("override_reason") or "").strip()

        if allergy_matches and not (override_ack and override_reason):
            return render(request, "pharmacy/prescribe.html", {
                "page_title": _("Prescribe"),
                "encounter": encounter,
                "drugs": drugs,
                "allergy_match": "; ".join(allergy_matches),
                "form_data": request.POST,
            })

        created = []
        billed_any = False
        for row in prescription_rows:
            drug = row["drug"]
            instructions = row["instructions"]
            match = check_allergy(encounter.patient, drug)
            if match and override_ack:
                instructions = (
                    f"[ALLERGY OVERRIDE — {match}] reason: {override_reason}\n{instructions}"
                ).strip()
            rx = Prescription.objects.create(
                encounter=encounter,
                drug=drug,
                dose=row["dose"],
                frequency=row["frequency"],
                duration_days=row["duration_days"],
                quantity=row["quantity"],
                instructions=instructions,
                prescribed_by=request.user,
                created_by=request.user,
            )
            created.append(rx)
            if match and override_ack:
                audit(request, "UPDATE", rx, change="allergy_override", match=match, reason=override_reason)
            billed_any = bill_prescription(rx) or billed_any
            notify_role(
                "PHARMACY",
                exclude_actor=request.user,
                kind="RX_READY",
                level="INFO",
                title=_("New prescription to dispense"),
                body=f"{encounter.patient.full_name} · {drug.generic_name} {drug.strength} × {row['quantity']}",
                url=f"/pharmacy/rx/{rx.pk}/dispense/",
                entity_type="Prescription",
                entity_id=rx.pk,
            )

        if billed_any:
            messages.success(
                request,
                _("%(count)s prescription(s) added and billed.") % {"count": len(created)},
            )
        else:
            messages.success(request, _("%(count)s prescription(s) added.") % {"count": len(created)})
        # Tell the front desk a new chargeable line was added so they can collect.
        if billed_any:
            try:
                from apps.billing.models import Invoice
                inv = Invoice.objects.filter(encounter=encounter).first()
                if inv is not None and inv.balance_tzs > 0:
                    summary = ", ".join(
                        f"{rx.drug.generic_name} {rx.drug.strength} × {rx.quantity}"
                        for rx in created
                    )
                    notify_role(
                        ["RECEPTIONIST", "FINANCE"],
                        exclude_actor=request.user,
                        kind="PAYMENT_REQUIRED",
                        level="WARNING",
                        title=_("Drugs to collect payment for"),
                        body=f"{encounter.patient.full_name} · {summary} · {inv.balance_tzs} TZS",
                        url=f"/billing/invoices/{inv.pk}/",
                        entity_type="Invoice",
                        entity_id=inv.pk,
                    )
            except Exception:
                pass
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
        _prescriptions_for_user(request.user).select_related("encounter__patient", "encounter__clinician", "drug"),
        pk=pk,
    )
    html = render_to_string("pharmacy/rx_print.html", {
        "rx": rx,
        "now": timezone.now(),
    }, request=request)
    return render_pdf(html, filename=f"rx-{rx.pk}.pdf")


# ─── Drug catalogue management (PHARMACY, ADMIN) ──────────────────

DRUG_FORMS = ["TABLET", "CAPSULE", "SYRUP", "INJECTION", "CREAM", "DROPS", "OTHER"]


def _parse_expiry_month(raw: str):
    """Parse pharmacy expiry input and store it as the month's final day."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Expiry month is required.")
    for fmt in ("%Y-%m", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            last_day = calendar.monthrange(parsed.year, parsed.month)[1]
            return parsed.replace(day=last_day)
        except ValueError:
            continue
    raise ValueError("Expiry must be YYYY-MM.")


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
    """Add a drug to the formulary, optionally with a first stock batch.

    If batch_number / expiry month / quantity_on_hand are supplied, a StockItem
    is created in the same transaction so the pharmacy doesn't have to do a
    separate "receive stock" step on the very first batch.
    """
    if request.method == "POST":
        try:
            drug = _populate_drug(Drug(), request.POST)
            drug.full_clean()

            batch_number = (request.POST.get("batch_number") or "").strip()
            expiry_raw = (request.POST.get("expiry_date") or "").strip()
            qty_raw = (request.POST.get("quantity_on_hand") or "").strip()

            with transaction.atomic():
                drug.save()
                if batch_number or expiry_raw or qty_raw:
                    # Any of these means the user intends to register a batch.
                    # Validate the trio together so partial input is rejected.
                    if not (batch_number and expiry_raw and qty_raw):
                        raise ValueError("Batch number, expiry month and quantity are all required to register a batch.")
                    expiry = _parse_expiry_month(expiry_raw)
                    if expiry <= timezone.now().date():
                        raise ValueError("Expiry month must be in the future.")
                    try:
                        qty = int(qty_raw)
                    except ValueError:
                        raise ValueError("Quantity must be a whole number.")
                    if qty <= 0:
                        raise ValueError("Quantity must be positive.")
                    item = StockItem.objects.create(
                        drug=drug, batch_number=batch_number,
                        expiry_date=expiry, quantity_on_hand=qty,
                    )
                    StockMovement.objects.create(
                        stock_item=item, movement_type="RECEIVE",
                        quantity=qty, performed_by=request.user,
                    )
                    messages.success(
                        request,
                        _("Drug '%(n)s' added with batch %(b)s (%(q)s units).")
                        % {"n": drug.generic_name, "b": batch_number, "q": qty},
                    )
                else:
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
            messages.error(request, _("Batch number, positive quantity, and expiry month are all required."))
            return redirect("pharmacy:stock_receive", drug_pk=drug.pk)
        try:
            expiry = _parse_expiry_month(expiry_raw)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("pharmacy:stock_receive", drug_pk=drug.pk)
        if expiry <= timezone.now().date():
            messages.error(request, _("Expiry month must be in the future."))
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


@require_POST
@requires_role("PHARMACY", "ADMIN")
def drug_delete(request, pk):
    """Delete a drug from the formulary when it has no protected history."""
    drug = get_object_or_404(Drug, pk=pk)
    label = str(drug)
    drug_pk = drug.pk
    try:
        drug.delete()
        audit(request, "DELETE", entity_type="Drug", entity_id=drug_pk, change="delete_drug", drug=label)
        messages.success(request, _("Drug '%(n)s' deleted.") % {"n": label})
    except ProtectedError:
        messages.error(
            request,
            _("Drug '%(n)s' has prescription history and cannot be deleted. Edit it instead if details are wrong.")
            % {"n": label},
        )
    return redirect("pharmacy:catalogue")


# ─── Stock adjustment ─────────────────────────────────────────────

@requires_role("CLINICIAN", "ADMIN")
def rx_edit(request, pk):
    """Edit a prescription before it has been dispensed.

    Runs the same allergy check as prescribing (changing the drug must not
    bypass it) and re-prices the invoice line.
    """
    rx = get_object_or_404(
        _prescriptions_for_user(request.user).select_related("encounter__patient", "drug"), pk=pk,
    )
    if rx.status != "PRESCRIBED":
        messages.error(request, _("Only un-dispensed prescriptions can be edited. Use Void to amend a dispensed Rx."))
        return redirect("encounters:detail", pk=rx.encounter_id)
    if rx.encounter.status == "FINALISED":
        messages.error(request, _("Encounter is finalised. Reopen it to change a prescription."))
        return redirect("encounters:detail", pk=rx.encounter_id)
    drugs = Drug.objects.filter(is_active=True).order_by("generic_name")
    if request.method == "POST":
        try:
            quantity = int(request.POST.get("quantity") or 0)
            duration_days = int(request.POST.get("duration_days") or 0)
        except (TypeError, ValueError):
            messages.error(request, _("Quantity and duration must be whole numbers."))
            return redirect("pharmacy:rx_edit", pk=rx.pk)
        dose = (request.POST.get("dose") or "").strip()
        frequency = (request.POST.get("frequency") or "").strip()
        if not dose or not frequency or quantity <= 0 or duration_days <= 0:
            messages.error(request, _("Dose, frequency, quantity and duration are all required."))
            return redirect("pharmacy:rx_edit", pk=rx.pk)
        drug_pk = parse_uuid(request.POST.get("drug"))
        drug = Drug.objects.filter(pk=drug_pk, is_active=True).first() if drug_pk else None
        if drug is None:
            messages.error(request, _("Select a drug from the formulary."))
            return redirect("pharmacy:rx_edit", pk=rx.pk)

        instructions = request.POST.get("instructions", "")
        match = check_allergy(rx.encounter.patient, drug)
        override_ack = request.POST.get("allergy_override") == "on"
        override_reason = (request.POST.get("override_reason") or "").strip()
        if match and not (override_ack and override_reason):
            return render(request, "pharmacy/prescribe.html", {
                "page_title": _("Edit prescription"),
                "encounter": rx.encounter,
                "drugs": drugs,
                "rx": rx,
                "allergy_match": match,
                "form_data": request.POST,
            })
        if match:
            instructions = f"[ALLERGY OVERRIDE — {match}] reason: {override_reason}\n{instructions}".strip()

        previous_drug, previous_quantity = rx.drug, rx.quantity
        with transaction.atomic():
            rx.drug = drug
            rx.dose = dose
            rx.frequency = frequency
            rx.duration_days = duration_days
            rx.quantity = quantity
            rx.instructions = instructions
            rx.updated_by = request.user
            rx.save()
            update_prescription_charge(rx, previous_drug=previous_drug, previous_quantity=previous_quantity)
        if match:
            audit(request, "UPDATE", rx, change="allergy_override", match=match, reason=override_reason)
        messages.success(request, _("Prescription updated."))
        return redirect("encounters:detail", pk=rx.encounter_id)
    return render(request, "pharmacy/prescribe.html", {
        "page_title": _("Edit prescription"),
        "encounter": rx.encounter,
        "drugs": drugs,
        "rx": rx,
        "form_data": {
            "drug": str(rx.drug_id), "dose": rx.dose, "frequency": rx.frequency,
            "duration_days": rx.duration_days, "quantity": rx.quantity, "instructions": rx.instructions,
        },
    })


@require_POST
@requires_role("CLINICIAN", "ADMIN")
def rx_cancel(request, pk):
    """Cancel an un-dispensed prescription with a reason; its charge is removed."""
    rx = get_object_or_404(_prescriptions_for_user(request.user), pk=pk)
    if rx.status != "PRESCRIBED":
        messages.error(request, _("Only un-dispensed prescriptions can be cancelled. Use Void to reverse a dispensed Rx."))
        return redirect("encounters:detail", pk=rx.encounter_id)
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, _("A reason is required to cancel a prescription."))
        return redirect("encounters:detail", pk=rx.encounter_id)
    with transaction.atomic():
        rx.status = "CANCELLED"
        rx.instructions = (f"[CANCELLED by {request.user}: {reason}]\n" + rx.instructions).strip()
        rx.updated_by = request.user
        rx.save(update_fields=["status", "instructions", "updated_by", "updated_at"])
        invoice = remove_prescription_charge(rx)
    audit(request, "UPDATE", rx, change="cancel_prescription", reason=reason)
    messages.success(request, _("Prescription cancelled. Reason recorded."))
    _charge_removed_message(request, invoice)
    return redirect("encounters:detail", pk=rx.encounter_id)


@require_POST
@requires_role("PHARMACY", "ADMIN")
def rx_void(request, pk):
    """Void a dispensed prescription. Returns dispensed quantities back to stock."""
    rx = get_object_or_404(Prescription.objects.select_related("encounter"), pk=pk)
    if rx.status not in ("DISPENSED", "PARTIALLY_DISPENSED"):
        messages.error(request, _("Only dispensed prescriptions can be voided."))
        return redirect("pharmacy:rx_queue")
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, _("A reason is required to void a dispensed prescription."))
        return redirect("pharmacy:rx_queue")
    with transaction.atomic():
        # Lock the Rx so a double-submitted void can't return stock twice.
        rx = Prescription.objects.select_for_update().get(pk=rx.pk)
        if rx.status not in ("DISPENSED", "PARTIALLY_DISPENSED"):
            messages.error(request, _("Only dispensed prescriptions can be voided."))
            return redirect("pharmacy:rx_queue")
        # Reverse every dispense by returning stock and recording a corrective movement.
        for d in rx.dispenses.select_related("stock_item").all():
            stock = StockItem.objects.select_for_update().get(pk=d.stock_item_id)
            stock.quantity_on_hand += d.quantity_dispensed
            stock.save(update_fields=["quantity_on_hand"])
            StockMovement.objects.create(
                stock_item=stock,
                movement_type="ADJUST",
                quantity=d.quantity_dispensed,
                reference_id=rx.pk,
                notes=f"Void of dispensed prescription: {reason}",
                performed_by=request.user,
            )
        rx.status = "CANCELLED"
        rx.instructions = (
            f"[VOIDED by {request.user}: {reason}]\n" + rx.instructions
        ).strip()
        rx.updated_by = request.user
        rx.save(update_fields=["status", "instructions", "updated_by", "updated_at"])
        invoice = remove_prescription_charge(rx)
    audit(request, "UPDATE", rx, change="void_dispensed_prescription", reason=reason)
    messages.success(request, _("Prescription voided and stock returned. Reason recorded."))
    _charge_removed_message(request, invoice)
    return redirect("pharmacy:rx_queue")


@requires_role("PHARMACY", "ADMIN")
def stock_edit(request, item_pk):
    """Edit an existing stock batch (correct typos in batch number, expiry month,
    or quantity). Quantity changes are recorded as a StockMovement so the audit
    trail remains complete."""
    item = get_object_or_404(StockItem.objects.select_related("drug"), pk=item_pk)
    if request.method == "POST":
        batch_number = (request.POST.get("batch_number") or "").strip()
        expiry_raw = (request.POST.get("expiry_date") or "").strip()
        qty_raw = (request.POST.get("quantity_on_hand") or "").strip()
        if not batch_number or not expiry_raw or not qty_raw:
            messages.error(request, _("Batch number, expiry month and quantity are all required."))
            return redirect("pharmacy:stock_edit", item_pk=item.pk)
        try:
            expiry = _parse_expiry_month(expiry_raw)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("pharmacy:stock_edit", item_pk=item.pk)
        try:
            new_qty = int(qty_raw)
        except ValueError:
            messages.error(request, _("Quantity must be a whole number."))
            return redirect("pharmacy:stock_edit", item_pk=item.pk)
        if new_qty < 0:
            messages.error(request, _("Quantity cannot be negative."))
            return redirect("pharmacy:stock_edit", item_pk=item.pk)
        with transaction.atomic():
            # Lock the batch so a dispense running at the same moment can't be
            # silently overwritten by this edit.
            item = StockItem.objects.select_for_update().select_related("drug").get(pk=item.pk)
            qty_diff = new_qty - item.quantity_on_hand
            item.batch_number = batch_number
            item.expiry_date = expiry
            item.quantity_on_hand = new_qty
            item.save(update_fields=["batch_number", "expiry_date", "quantity_on_hand"])
            if qty_diff != 0:
                StockMovement.objects.create(
                    stock_item=item, movement_type="ADJUST",
                    quantity=qty_diff, performed_by=request.user,
                    notes="Batch details edited",
                )
        messages.success(request, _("Batch updated."))
        return redirect("pharmacy:drug_detail", pk=item.drug.pk)
    return render(request, "pharmacy/stock_edit.html", {
        "page_title": _("Edit batch"),
        "item": item,
    })


@require_POST
@requires_role("PHARMACY", "ADMIN")
def stock_adjust(request, item_pk):
    """Adjust stock quantity on an existing batch (e.g. breakage, count correction).

    The new quantity must be given explicitly (a blank field is an error,
    never "zero"), and the reason is stored on the stock movement.
    """
    item = get_object_or_404(StockItem.objects.select_related("drug"), pk=item_pk)
    raw_qty = (request.POST.get("new_quantity") or "").strip()
    try:
        new_qty = int(raw_qty)
    except (TypeError, ValueError):
        messages.error(request, _("Enter the new quantity on hand as a whole number."))
        return redirect("pharmacy:drug_detail", pk=item.drug.pk)
    if new_qty < 0:
        messages.error(request, _("Stock quantity cannot be negative."))
        return redirect("pharmacy:drug_detail", pk=item.drug.pk)
    reason_text = (request.POST.get("reason") or "").strip()
    if not reason_text:
        messages.error(request, _("A reason is required for stock adjustments."))
        return redirect("pharmacy:drug_detail", pk=item.drug.pk)
    reason_code = None
    reason_pk = parse_uuid(request.POST.get("reason_code"))
    if reason_pk:
        reason_code = ReasonCode.objects.filter(pk=reason_pk, category="STOCK_ADJUSTMENT").first()

    with transaction.atomic():
        item = StockItem.objects.select_for_update().select_related("drug").get(pk=item.pk)
        diff = new_qty - item.quantity_on_hand
        if diff == 0:
            messages.info(request, _("Quantity unchanged — nothing to adjust."))
            return redirect("pharmacy:drug_detail", pk=item.drug.pk)
        item.quantity_on_hand = new_qty
        item.save(update_fields=["quantity_on_hand"])
        StockMovement.objects.create(
            stock_item=item,
            movement_type="WRITE_OFF" if reason_code is not None and reason_code.code == "ADJ-EXPIRED" else "ADJUST",
            quantity=diff,
            reason=reason_code,
            notes=reason_text,
            performed_by=request.user,
        )
    messages.success(request, _("Stock adjusted by %(d)s units. Reason: %(r)s") % {"d": diff, "r": reason_text})
    return redirect("pharmacy:drug_detail", pk=item.drug.pk)
