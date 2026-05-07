"""Primetel CMS — Pharmacy Views."""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.decorators import requires_role
from apps.core.models import AuditLog
from apps.core.notifications import notify_role, notify_user
from apps.core.pdf import render_pdf
from apps.encounters.models import Encounter

from .allergy import check_allergy
from .models import Dispense, Drug, Prescription, StockItem, StockMovement


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


def _bill_prescription(rx):
    """Add a billing line for a prescription to the encounter's invoice.

    Charged at the drug's catalogue unit_price_tzs. If the encounter has no
    invoice yet (rare — the encounter open flow auto-charges) this is a no-op
    so we don't accidentally create a stray invoice. Returns True if a line
    was added.
    """
    try:
        from apps.billing.models import Invoice, InvoiceLine
        invoice = Invoice.objects.filter(encounter_id=rx.encounter_id).first()
        if invoice is None or invoice.status in ("CANCELLED", "WAIVED"):
            return False
        unit_price = rx.drug.unit_price_tzs or Decimal("0")
        InvoiceLine.objects.create(
            invoice=invoice,
            description=f"{rx.drug.generic_name} {rx.drug.strength} ({rx.drug.get_form_display()})",
            quantity=Decimal(rx.quantity),
            unit_price_tzs=unit_price,
        )
        invoice.recalculate()
        # Reopen a paid invoice so the new balance shows up correctly.
        if invoice.balance_tzs > 0 and invoice.status == "PAID":
            invoice.status = "PARTIALLY_PAID"
            invoice.save(update_fields=["status"])
        return True
    except Exception:
        # Never break Rx creation on a billing hiccup.
        return False


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
    # Hard payment gate: prescriptions written only after consultation paid.
    from apps.billing.models import Invoice
    inv = Invoice.objects.filter(encounter=encounter).first()
    if inv is not None and inv.status not in ("PAID", "WAIVED") and inv.balance_tzs > 0:
        messages.error(request, _("Awaiting payment confirmation. The receptionist must record the consultation payment before prescriptions can be written."))
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

        rx = Prescription.objects.create(
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
        billed = _bill_prescription(rx)
        if billed:
            messages.success(
                request,
                _("Prescription added and billed at %(p)s TZS per unit (× %(q)s).") % {
                    "p": drug.unit_price_tzs or 0, "q": quantity,
                },
            )
        else:
            messages.success(request, _("Prescription added."))
        notify_role(
            "PHARMACY",
            exclude_actor=request.user,
            kind="RX_READY",
            level="INFO",
            title=_("New prescription to dispense"),
            body=f"{encounter.patient.full_name} · {drug.generic_name} {drug.strength} × {quantity}",
            url=f"/pharmacy/rx/{rx.pk}/dispense/",
            entity_type="Prescription",
            entity_id=rx.pk,
        )
        # Tell the front desk a new chargeable line was added so they can collect.
        if billed:
            try:
                from apps.billing.models import Invoice
                inv = Invoice.objects.filter(encounter=encounter).first()
                if inv is not None and inv.balance_tzs > 0:
                    notify_role(
                        ["RECEPTIONIST", "FINANCE"],
                        exclude_actor=request.user,
                        kind="PAYMENT_REQUIRED",
                        level="WARNING",
                        title=_("Drugs to collect payment for"),
                        body=f"{encounter.patient.full_name} · {drug.generic_name} {drug.strength} × {quantity} · {inv.balance_tzs} TZS",
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
    """Add a drug to the formulary, optionally with a first stock batch.

    If batch_number / expiry_date / quantity_on_hand are supplied, a StockItem
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
                        raise ValueError("Batch number, expiry date and quantity are all required to register a batch.")
                    try:
                        expiry = datetime.strptime(expiry_raw, "%Y-%m-%d").date()
                    except ValueError:
                        raise ValueError("Expiry date must be YYYY-MM-DD.")
                    if expiry <= timezone.now().date():
                        raise ValueError("Expiry date must be in the future.")
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

@requires_role("CLINICIAN", "ADMIN")
def rx_edit(request, pk):
    """Edit a prescription before it has been dispensed."""
    rx = get_object_or_404(Prescription.objects.select_related("encounter__patient", "drug"), pk=pk)
    if rx.status != "PRESCRIBED":
        messages.error(request, _("Only un-dispensed prescriptions can be edited. Use Void to amend a dispensed Rx."))
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
        drug = get_object_or_404(Drug, pk=request.POST.get("drug"))
        rx.drug = drug
        rx.dose = dose
        rx.frequency = frequency
        rx.duration_days = duration_days
        rx.quantity = quantity
        rx.instructions = request.POST.get("instructions", "")
        rx.updated_by = request.user
        rx.save()
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
    """Cancel an un-dispensed prescription with a reason."""
    rx = get_object_or_404(Prescription, pk=pk)
    if rx.status != "PRESCRIBED":
        messages.error(request, _("Only un-dispensed prescriptions can be cancelled. Use Void to reverse a dispensed Rx."))
        return redirect("encounters:detail", pk=rx.encounter_id)
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, _("A reason is required to cancel a prescription."))
        return redirect("encounters:detail", pk=rx.encounter_id)
    rx.status = "CANCELLED"
    rx.instructions = (f"[CANCELLED by {request.user}: {reason}]\n" + rx.instructions).strip()
    rx.updated_by = request.user
    rx.save(update_fields=["status", "instructions", "updated_by", "updated_at"])
    _audit(request, "UPDATE", rx, change="cancel_prescription", reason=reason)
    messages.success(request, _("Prescription cancelled. Reason recorded."))
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
    from django.db import transaction
    with transaction.atomic():
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
                performed_by=request.user,
            )
        rx.status = "CANCELLED"
        rx.instructions = (
            f"[VOIDED by {request.user}: {reason}]\n" + rx.instructions
        ).strip()
        rx.updated_by = request.user
        rx.save(update_fields=["status", "instructions", "updated_by", "updated_at"])
    _audit(request, "UPDATE", rx, change="void_dispensed_prescription", reason=reason)
    messages.success(request, _("Prescription voided and stock returned. Reason recorded."))
    return redirect("pharmacy:rx_queue")


@requires_role("PHARMACY", "ADMIN")
def stock_edit(request, item_pk):
    """Edit an existing stock batch (correct typos in batch number, expiry date,
    or quantity). Quantity changes are recorded as a StockMovement so the audit
    trail remains complete."""
    item = get_object_or_404(StockItem.objects.select_related("drug"), pk=item_pk)
    if request.method == "POST":
        batch_number = (request.POST.get("batch_number") or "").strip()
        expiry_raw = (request.POST.get("expiry_date") or "").strip()
        qty_raw = (request.POST.get("quantity_on_hand") or "").strip()
        if not batch_number or not expiry_raw or not qty_raw:
            messages.error(request, _("Batch number, expiry date and quantity are all required."))
            return redirect("pharmacy:stock_edit", item_pk=item.pk)
        try:
            expiry = datetime.strptime(expiry_raw, "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, _("Expiry date must be YYYY-MM-DD."))
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
            qty_diff = new_qty - item.quantity_on_hand
            item.batch_number = batch_number
            item.expiry_date = expiry
            item.quantity_on_hand = new_qty
            item.save(update_fields=["batch_number", "expiry_date", "quantity_on_hand"])
            if qty_diff != 0:
                StockMovement.objects.create(
                    stock_item=item, movement_type="ADJUST",
                    quantity=qty_diff, performed_by=request.user,
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
